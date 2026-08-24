use anyhow::Result;
use chrono::Utc;
use sqlx::{PgPool, postgres::PgPoolOptions};
use uuid::Uuid;

pub async fn create_pool(url: &str) -> Result<PgPool> {
    let pool = PgPoolOptions::new()
        .max_connections(12)
        .connect(url)
        .await?;
    Ok(pool)
}

// ── Job status updates ────────────────────────────────────────────────────────

pub async fn mark_processing(pool: &PgPool, job_id: Uuid) -> Result<()> {
    sqlx::query("UPDATE upload_jobs SET status='processing' WHERE id=$1")
        .bind(job_id)
        .execute(pool)
        .await?;
    Ok(())
}

pub async fn mark_done(pool: &PgPool, job_id: Uuid) -> Result<()> {
    sqlx::query("UPDATE upload_jobs SET status='done', completed_at=$1 WHERE id=$2")
        .bind(Utc::now())
        .bind(job_id)
        .execute(pool)
        .await?;
    Ok(())
}

pub async fn mark_failed(pool: &PgPool, job_id: Uuid, error: &str) -> Result<()> {
    sqlx::query("UPDATE upload_jobs SET status='failed', error=$1 WHERE id=$2")
        .bind(error)
        .bind(job_id)
        .execute(pool)
        .await?;
    Ok(())
}

// ── LPC row persistence ───────────────────────────────────────────────────────

pub struct LpcRow {
    pub job_id:                 Uuid,
    pub branch:                 String,
    pub stage:                  String,
    pub student_name:           String,
    pub class_sec:              String,
    pub roll_no:                String,
    pub month:                  String,
    pub domain:                 String,
    pub c1:                     Option<i32>,
    pub c2:                     Option<i32>,
    pub c3:                     Option<i32>,
    pub c4:                     Option<i32>,
    pub observational_anecdote: Option<String>,
    pub strengths:              Option<String>,
    pub focus_next_month:       Option<String>,
    pub parent_sign:            Option<bool>,
    pub teacher_sign:           Option<bool>,
    pub principal_sign:         Option<bool>,
    pub source_pdf:             String,
}

/// Bulk insert inside a single transaction.
pub async fn insert_lpc_rows(pool: &PgPool, rows: Vec<LpcRow>) -> Result<usize> {
    if rows.is_empty() {
        return Ok(0);
    }
    let count = rows.len();
    let mut tx = pool.begin().await?;

    for r in rows {
        sqlx::query(r#"
            INSERT INTO lpc_rows (
                id, job_id, branch, stage, student_name, class_sec, roll_no,
                month, domain, c1, c2, c3, c4,
                observational_anecdote, strengths, focus_next_month,
                parent_sign, teacher_sign, principal_sign, source_pdf, created_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                $14,$15,$16,$17,$18,$19,$20, NOW()
            )
        "#)
        .bind(Uuid::new_v4())
        .bind(r.job_id)
        .bind(&r.branch)
        .bind(&r.stage)
        .bind(&r.student_name)
        .bind(&r.class_sec)
        .bind(&r.roll_no)
        .bind(&r.month)
        .bind(&r.domain)
        .bind(r.c1).bind(r.c2).bind(r.c3).bind(r.c4)
        .bind(&r.observational_anecdote)
        .bind(&r.strengths)
        .bind(&r.focus_next_month)
        .bind(r.parent_sign)
        .bind(r.teacher_sign)
        .bind(r.principal_sign)
        .bind(&r.source_pdf)
        .execute(&mut *tx)
        .await?;
    }

    tx.commit().await?;
    Ok(count)
}
