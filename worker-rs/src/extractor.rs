use anyhow::{Context, Result};
use base64::{Engine, engine::general_purpose::STANDARD as B64};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use tokio::process::Command;

use crate::db::LpcRow;

// ── OpenRouter request/response types ────────────────────────────────────────

#[derive(Serialize)]
struct OrRequest<'a> {
    model:       &'a str,
    max_tokens:  u32,
    messages:    Vec<OrMessage<'a>>,
}

#[derive(Serialize)]
struct OrMessage<'a> {
    role:    &'a str,
    content: Vec<OrContent>,
}

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum OrContent {
    Text { text: String },
    ImageUrl { image_url: OrImageUrl },
}

#[derive(Serialize)]
struct OrImageUrl {
    url: String,
}

#[derive(Deserialize)]
struct OrResponse {
    choices: Vec<OrChoice>,
}

#[derive(Deserialize)]
struct OrChoice {
    message: OrMsg,
}

#[derive(Deserialize)]
struct OrMsg {
    content: String,
}

// ── Extracted LPC data ────────────────────────────────────────────────────────

#[derive(Deserialize, Debug, Default)]
pub struct LpcExtraction {
    pub stage:       Option<String>,
    pub student_name: Option<String>,
    pub class_sec:   Option<String>,
    pub roll_no:     Option<String>,
    pub months:      Vec<MonthData>,
    pub parent_sign_present:    Option<bool>,
    pub teacher_sign_present:   Option<bool>,
    pub principal_sign_present: Option<bool>,
}

#[derive(Deserialize, Debug)]
pub struct MonthData {
    pub month:   String,
    // Model returns domains as object: { "Early Literacy": { c1, c2, ... }, ... }
    pub domains: HashMap<String, DomainScores>,
}

#[derive(Deserialize, Debug, Default)]
pub struct DomainScores {
    pub c1:                     Option<i32>,
    pub c2:                     Option<i32>,
    pub c3:                     Option<i32>,
    pub c4:                     Option<i32>,
    pub observational_anecdote: Option<String>,
    pub strengths:              Option<String>,
    pub focus_next_month:       Option<String>,
}

// ── PDF → JPEG pages via pdftoppm ────────────────────────────────────────────

pub async fn pdf_to_base64_jpegs(pdf_bytes: &[u8]) -> Result<Vec<String>> {
    use tokio::fs;

    // write PDF to temp file
    let tmp_dir  = tempfile_dir().await?;
    let pdf_path = tmp_dir.join("input.pdf");
    let out_pfx  = tmp_dir.join("page");

    fs::write(&pdf_path, pdf_bytes).await?;

    // pdftoppm -jpeg -r 150 input.pdf page  →  page-01.jpg, page-02.jpg …
    let status = Command::new("pdftoppm")
        .args(["-jpeg", "-r", "150",
               pdf_path.to_str().unwrap(),
               out_pfx.to_str().unwrap()])
        .status()
        .await
        .context("pdftoppm not found — install poppler")?;

    if !status.success() {
        anyhow::bail!("pdftoppm exited {status}");
    }

    // collect output files
    let mut entries: Vec<_> = {
        let mut rd = fs::read_dir(&tmp_dir).await?;
        let mut v  = Vec::new();
        while let Some(e) = rd.next_entry().await? {
            let p = e.path();
            if p.extension().map(|x| x == "jpg" || x == "jpeg").unwrap_or(false) {
                v.push(p);
            }
        }
        v
    };
    entries.sort();

    let mut result = Vec::with_capacity(entries.len());
    for path in &entries {
        let bytes = fs::read(path).await?;
        result.push(format!("data:image/jpeg;base64,{}", B64.encode(&bytes)));
    }

    // cleanup
    let _ = fs::remove_dir_all(&tmp_dir).await;

    Ok(result)
}

async fn tempfile_dir() -> Result<std::path::PathBuf> {
    let dir = std::env::temp_dir().join(format!("rysen-{}", uuid::Uuid::new_v4()));
    tokio::fs::create_dir_all(&dir).await?;
    Ok(dir)
}

// ── OpenRouter call ───────────────────────────────────────────────────────────

static PROMPT: &str = include_str!("../../prompts/lpc_prompt.txt");

pub async fn call_openrouter(
    client:    &reqwest::Client,
    api_key:   &str,
    model:     &str,
    images_b64: Vec<String>,
) -> Result<LpcExtraction> {
    // build content: prompt text + all page images
    let mut content = vec![OrContent::Text { text: PROMPT.to_owned() }];
    for img in images_b64 {
        content.push(OrContent::ImageUrl {
            image_url: OrImageUrl { url: img },
        });
    }

    let body = OrRequest {
        model,
        max_tokens: 8192,
        messages: vec![OrMessage { role: "user", content }],
    };

    let raw_resp = client
        .post("https://openrouter.ai/api/v1/chat/completions")
        .bearer_auth(api_key)
        .json(&body)
        .send()
        .await
        .context("OpenRouter request failed")?;

    let status = raw_resp.status();
    // Use bytes() — more robust than text() for large or non-UTF8 responses
    let body_bytes = raw_resp.bytes().await.context("OpenRouter body stream failed")?;
    let body_text  = String::from_utf8_lossy(&body_bytes).into_owned();

    tracing::debug!(status = %status, body_len = body_bytes.len(), "OpenRouter raw response");

    if !status.is_success() {
        anyhow::bail!("OpenRouter HTTP {status}: {}", &body_text[..body_text.len().min(400)]);
    }

    let resp: OrResponse = serde_json::from_str(&body_text)
        .with_context(|| format!(
            "OpenRouter JSON parse (HTTP {status}, {} bytes): {}",
            body_bytes.len(),
            &body_text[..body_text.len().min(600)]
        ))?;

    let raw = resp.choices
        .into_iter()
        .next()
        .context("empty choices")?
        .message
        .content;

    parse_extraction(&raw)
}

fn parse_extraction(raw: &str) -> Result<LpcExtraction> {
    // strip markdown fences if model wrapped output
    let cleaned = raw
        .trim()
        .trim_start_matches("```json")
        .trim_start_matches("```")
        .trim_end_matches("```")
        .trim();

    serde_json::from_str::<LpcExtraction>(cleaned)
        .or_else(|_| {
            // fallback: return empty extraction rather than hard fail
            tracing::warn!(raw = %cleaned, "JSON parse failed — returning empty extraction");
            Ok(LpcExtraction::default())
        })
}

// ── Build DB rows from extraction ─────────────────────────────────────────────

pub fn extraction_to_rows(
    job_id:       uuid::Uuid,
    branch:       &str,
    student_name: &str,
    class_sec:    &str,
    roll_no:      &str,
    filename:     &str,
    ext:          LpcExtraction,
) -> Vec<LpcRow> {
    let stage        = ext.stage.unwrap_or_else(|| "unknown".into());
    let student_name = if student_name.is_empty() {
        ext.student_name.unwrap_or_default()
    } else {
        student_name.to_owned()
    };
    let class_sec = if class_sec.is_empty() {
        ext.class_sec.unwrap_or_default()
    } else {
        class_sec.to_owned()
    };
    let roll_no = if roll_no.is_empty() {
        ext.roll_no.unwrap_or_default()
    } else {
        roll_no.to_owned()
    };

    let mut rows = Vec::new();
    for m in ext.months {
        let month = m.month.clone();
        for (domain_name, d) in m.domains {
            rows.push(LpcRow {
                job_id,
                branch:   branch.to_owned(),
                stage:    stage.clone(),
                student_name: student_name.clone(),
                class_sec:    class_sec.clone(),
                roll_no:      roll_no.clone(),
                month:        month.clone(),
                domain:       domain_name,
                c1: d.c1, c2: d.c2, c3: d.c3, c4: d.c4,
                observational_anecdote: d.observational_anecdote,
                strengths:              d.strengths,
                focus_next_month:       d.focus_next_month,
                parent_sign:    ext.parent_sign_present,
                teacher_sign:   ext.teacher_sign_present,
                principal_sign: ext.principal_sign_present,
                source_pdf:     filename.to_owned(),
            });
        }
    }
    rows
}
