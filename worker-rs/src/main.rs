mod circuit_breaker;
mod config;
mod db;
mod extractor;
mod telemetry;
mod worker;

use std::sync::Arc;

use axum::{
    extract::{Multipart, State},
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use serde_json::{json, Value};
use tokio::sync::{mpsc, Mutex, Semaphore};
use tokio_util::sync::CancellationToken;
use tracing::info;
use uuid::Uuid;

use crate::{
    circuit_breaker::CircuitBreaker,
    config::Config,
    worker::{PdfJob, WorkerCtx},
};

// ── Shared axum state ─────────────────────────────────────────────────────────

#[derive(Clone)]
struct AppState {
    tx:      mpsc::Sender<PdfJob>,
    cb:      Arc<CircuitBreaker>,
    capacity: usize,
}

// ── HTTP handlers ─────────────────────────────────────────────────────────────

/// POST /submit  —  called by FastAPI instead of Celery
///
/// Multipart fields:
///   job_id       (UUID string, created by FastAPI)
///   branch       (string)
///   student_name (string)
///   class_sec    (string)
///   roll_no      (string)
///   file         (PDF bytes)
async fn submit(
    State(state): State<AppState>,
    mut multipart: Multipart,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let mut job_id       = None::<Uuid>;
    let mut branch       = String::new();
    let mut student_name = String::new();
    let mut class_sec    = String::new();
    let mut roll_no      = String::new();
    let mut filename     = String::from("upload.pdf");
    let mut pdf_bytes    = Vec::<u8>::new();

    while let Some(field) = multipart.next_field().await.map_err(bad)? {
        match field.name() {
            Some("job_id") => {
                let s = field.text().await.map_err(bad)?;
                job_id = Some(Uuid::parse_str(&s).map_err(bad)?);
            }
            Some("branch")       => branch       = field.text().await.map_err(bad)?,
            Some("student_name") => student_name = field.text().await.map_err(bad)?,
            Some("class_sec")    => class_sec    = field.text().await.map_err(bad)?,
            Some("roll_no")      => roll_no      = field.text().await.map_err(bad)?,
            Some("file") => {
                if let Some(name) = field.file_name() {
                    filename = name.to_owned();
                }
                pdf_bytes = field.bytes().await.map_err(bad)?.to_vec();
            }
            _ => {}
        }
    }

    let job_id = job_id.ok_or_else(|| bad("missing job_id"))?;
    if pdf_bytes.is_empty() {
        return Err(bad("missing file"));
    }
    if branch.is_empty() {
        return Err(bad("missing branch"));
    }

    let job = PdfJob { job_id, branch, student_name, class_sec, roll_no, filename, pdf_bytes };

    match state.tx.try_send(job) {
        Ok(_) => {
            info!(%job_id, "job queued");
            Ok(Json(json!({ "queued": true, "job_id": job_id.to_string() })))
        }
        Err(mpsc::error::TrySendError::Full(_)) => {
            Err((
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({
                    "error": "queue full",
                    "capacity": state.capacity,
                    "hint": "retry in a few seconds"
                })),
            ))
        }
        Err(e) => Err(bad(e)),
    }
}

/// GET /health
async fn health(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "status":          "ok",
        "service":         "rysen-worker",
        "circuit_breaker": state.cb.state_str(),
        "cb_failures":     state.cb.failure_count(),
        "queue_capacity":  state.capacity,
    }))
}

fn bad(e: impl std::fmt::Display) -> (StatusCode, Json<Value>) {
    (StatusCode::BAD_REQUEST, Json(json!({ "error": e.to_string() })))
}

// ── Entry point ───────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cfg = Config::from_env()?;
    telemetry::init(cfg.otlp_endpoint.as_deref())?;

    info!(
        addr            = %cfg.listen_addr,
        workers         = cfg.worker_count,
        channel_cap     = cfg.channel_capacity,
        llm_concurrency = cfg.llm_concurrency,
        cb_threshold    = cfg.cb_failure_threshold,
        "rysen-worker starting"
    );

    // ── Infrastructure ────────────────────────────────────────────────────────
    let pool = db::create_pool(&cfg.database_url).await?;
    info!("PostgreSQL pool ready");

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(180))
        .build()?;

    let sem = Arc::new(Semaphore::new(cfg.llm_concurrency));
    let cb  = CircuitBreaker::new(cfg.cb_failure_threshold, cfg.cb_reset_timeout_secs);

    // ── Bounded job channel (backpressure) ────────────────────────────────────
    let (tx, rx) = mpsc::channel::<PdfJob>(cfg.channel_capacity);
    let rx       = Arc::new(Mutex::new(rx));

    // ── Cancellation token for graceful shutdown ──────────────────────────────
    let token = CancellationToken::new();

    // ── Spawn worker tasks ────────────────────────────────────────────────────
    let ctx = WorkerCtx {
        pool:    pool.clone(),
        client,
        api_key: Arc::new(cfg.openrouter_api_key.clone()),
        model:   Arc::new(cfg.openrouter_model.clone()),
        sem,
        cb:      cb.clone(),
    };
    let handles = worker::spawn_workers(cfg.worker_count, rx, ctx, token.clone());
    info!(count = cfg.worker_count, "worker tasks spawned");

    // ── Axum HTTP server ──────────────────────────────────────────────────────
    let app_state = AppState {
        tx,
        cb,
        capacity: cfg.channel_capacity,
    };

    let app = Router::new()
        .route("/submit", post(submit))
        .route("/health", get(health))
        .with_state(app_state);

    let listener = tokio::net::TcpListener::bind(&cfg.listen_addr).await?;
    info!(addr = %cfg.listen_addr, "HTTP server listening");

    // ── Graceful shutdown ─────────────────────────────────────────────────────
    let shutdown = {
        let token = token.clone();
        async move {
            tokio::signal::ctrl_c().await.expect("ctrl-c handler");
            info!("shutdown signal received — draining workers");
            token.cancel();
        }
    };

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown)
        .await?;

    // Wait for all worker tasks to finish draining
    for h in handles {
        let _ = h.await;
    }

    info!("rysen-worker shut down cleanly");
    Ok(())
}
