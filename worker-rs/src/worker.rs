use std::sync::Arc;
use tokio::sync::{Mutex, Semaphore};
use tokio_util::sync::CancellationToken;
use tracing::{Instrument, info, warn, error};
use uuid::Uuid;

use crate::{
    circuit_breaker::CircuitBreaker,
    db,
    extractor,
};

/// A PDF job received from the HTTP endpoint.
#[derive(Debug)]
pub struct PdfJob {
    pub job_id:       Uuid,
    pub branch:       String,
    pub student_name: String,
    pub class_sec:    String,
    pub roll_no:      String,
    pub filename:     String,
    pub pdf_bytes:    Vec<u8>,
}

/// Context shared across all worker tasks.
#[derive(Clone)]
pub struct WorkerCtx {
    pub pool:    sqlx::PgPool,
    pub client:  reqwest::Client,
    pub api_key: Arc<String>,
    pub model:   Arc<String>,
    pub sem:     Arc<Semaphore>,
    pub cb:      Arc<CircuitBreaker>,
}

/// Spawn `count` worker tasks, each draining from `rx`.
/// Stops cleanly when `token` is cancelled and the channel is drained.
pub fn spawn_workers(
    count:  usize,
    rx:     Arc<Mutex<tokio::sync::mpsc::Receiver<PdfJob>>>,
    ctx:    WorkerCtx,
    token:  CancellationToken,
) -> Vec<tokio::task::JoinHandle<()>> {
    (0..count)
        .map(|id| {
            let rx    = rx.clone();
            let ctx   = ctx.clone();
            let token = token.clone();
            tokio::spawn(async move {
                info!(worker_id = id, "worker started");
                loop {
                    // Receive next job OR exit if cancelled + channel empty
                    let job = {
                        let mut guard = rx.lock().await;
                        tokio::select! {
                            biased;
                            job = guard.recv() => match job {
                                Some(j) => j,
                                None    => {
                                    info!(worker_id = id, "channel closed — worker exiting");
                                    break;
                                }
                            },
                            _ = token.cancelled() => {
                                // drain remaining jobs before exit
                                match guard.try_recv() {
                                    Ok(j)  => j,
                                    Err(_) => {
                                        info!(worker_id = id, "cancelled + queue empty — exiting");
                                        break;
                                    }
                                }
                            }
                        }
                    };

                    let span = tracing::info_span!(
                        "process_job",
                        job_id  = %job.job_id,
                        branch  = %job.branch,
                        worker  = id,
                    );
                    process_job(job, &ctx).instrument(span).await;
                }
            })
        })
        .collect()
}

async fn process_job(job: PdfJob, ctx: &WorkerCtx) {
    info!(job_id = %job.job_id, filename = %job.filename, "processing");

    if let Err(e) = db::mark_processing(&ctx.pool, job.job_id).await {
        error!(?e, "failed to mark job processing");
    }

    match run_extraction(&job, ctx).await {
        Ok(rows) => {
            info!(job_id = %job.job_id, rows, "done");
            let _ = db::mark_done(&ctx.pool, job.job_id).await;
        }
        Err(e) => {
            error!(job_id = %job.job_id, error = %e, "extraction failed");
            let _ = db::mark_failed(&ctx.pool, job.job_id, &e.to_string()).await;
        }
    }
}

async fn run_extraction(job: &PdfJob, ctx: &WorkerCtx) -> anyhow::Result<usize> {
    // ── Circuit breaker check ─────────────────────────────────────────────────
    ctx.cb.check()?;

    // ── PDF → JPEG pages ──────────────────────────────────────────────────────
    let span = tracing::info_span!("pdf_to_images", job_id = %job.job_id);
    let images = extractor::pdf_to_base64_jpegs(&job.pdf_bytes)
        .instrument(span)
        .await?;

    info!(job_id = %job.job_id, pages = images.len(), "PDF converted to images");

    // ── Semaphore: max concurrent LLM calls ───────────────────────────────────
    let _permit = ctx.sem.acquire().await?;

    // ── OpenRouter call ───────────────────────────────────────────────────────
    let span = tracing::info_span!("openrouter_call", job_id = %job.job_id, model = %ctx.model);
    let result = extractor::call_openrouter(
        &ctx.client,
        &ctx.api_key,
        &ctx.model,
        images,
    )
    .instrument(span)
    .await;

    match result {
        Ok(extraction) => {
            ctx.cb.record_success();

            let rows = extractor::extraction_to_rows(
                job.job_id,
                &job.branch,
                &job.student_name,
                &job.class_sec,
                &job.roll_no,
                &job.filename,
                extraction,
            );

            let span = tracing::info_span!("db_insert", job_id = %job.job_id);
            let saved = db::insert_lpc_rows(&ctx.pool, rows)
                .instrument(span)
                .await?;

            Ok(saved)
        }
        Err(e) => {
            ctx.cb.record_failure();
            warn!(
                cb_state = ctx.cb.state_str(),
                failures = ctx.cb.failure_count(),
                "OpenRouter call failed"
            );
            Err(e)
        }
    }
}
