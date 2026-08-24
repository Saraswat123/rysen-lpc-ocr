/// Load test for rysen-worker's /submit endpoint.
///
/// Run:
///   cargo run --bin load-test -- --url http://localhost:9000 --jobs 50 --concurrency 10
///
/// Sends `jobs` PDF upload requests with `concurrency` in-flight at a time.
/// Reports p50 / p95 / p99 latencies and error rate.

use std::{
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::Instant,
};

use anyhow::Result;

const PDF_1PX: &[u8] = include_bytes!("../../tests/fixtures/minimal.pdf");

struct Args {
    url:         String,
    jobs:        u64,
    concurrency: usize,
}

fn parse_args() -> Args {
    let mut args = std::env::args().skip(1);
    let mut url         = "http://localhost:9000".to_string();
    let mut jobs: u64   = 20;
    let mut concurrency = 5;

    while let Some(key) = args.next() {
        match key.as_str() {
            "--url"         => url         = args.next().unwrap(),
            "--jobs"        => jobs        = args.next().unwrap().parse().unwrap(),
            "--concurrency" => concurrency = args.next().unwrap().parse().unwrap(),
            _               => {}
        }
    }
    Args { url, jobs, concurrency }
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = parse_args();
    println!(
        "Load test → {} | {} jobs | {} concurrent",
        args.url, args.jobs, args.concurrency
    );

    let client  = Arc::new(reqwest::Client::new());
    let errors  = Arc::new(AtomicU64::new(0));
    let latencies = Arc::new(tokio::sync::Mutex::new(Vec::<u64>::new()));
    let sem = Arc::new(tokio::sync::Semaphore::new(args.concurrency));

    let total_start = Instant::now();
    let mut handles = Vec::new();

    for i in 0..args.jobs {
        let client   = client.clone();
        let url      = format!("{}/submit", args.url);
        let errors   = errors.clone();
        let lats     = latencies.clone();
        let sem      = sem.clone();

        let h = tokio::spawn(async move {
            let _permit = sem.acquire().await.unwrap();
            let job_id  = uuid::Uuid::new_v4().to_string();

            let form = reqwest::multipart::Form::new()
                .text("job_id",       job_id)
                .text("branch",       "Jaisalmer")
                .text("student_name", format!("Student {i}"))
                .text("class_sec",    "5A")
                .text("roll_no",      i.to_string())
                .part("file", reqwest::multipart::Part::bytes(PDF_1PX.to_vec())
                    .file_name("test.pdf")
                    .mime_str("application/pdf").unwrap());

            let start = Instant::now();
            let res = client.post(&url).multipart(form).send().await;
            let elapsed_ms = start.elapsed().as_millis() as u64;

            match res {
                Ok(r) if r.status().is_success() => {
                    lats.lock().await.push(elapsed_ms);
                }
                Ok(r) => {
                    let status = r.status();
                    let body   = r.text().await.unwrap_or_default();
                    eprintln!("job {i}: HTTP {status} — {body}");
                    errors.fetch_add(1, Ordering::Relaxed);
                }
                Err(e) => {
                    eprintln!("job {i}: {e}");
                    errors.fetch_add(1, Ordering::Relaxed);
                }
            }
        });
        handles.push(h);
    }

    for h in handles {
        let _ = h.await;
    }

    let total = total_start.elapsed();
    let error_count = errors.load(Ordering::Relaxed);
    let mut lats = latencies.lock().await;
    lats.sort_unstable();

    let success = args.jobs - error_count;
    let p = |pct: f64| -> u64 {
        if lats.is_empty() { return 0; }
        let idx = ((pct / 100.0) * (lats.len() - 1) as f64).round() as usize;
        lats[idx.min(lats.len() - 1)]
    };

    println!("\n── Results ─────────────────────────────────────");
    println!("  Total jobs  : {}", args.jobs);
    println!("  Success     : {success}");
    println!("  Errors      : {error_count}");
    println!("  Total time  : {:.2}s", total.as_secs_f64());
    println!("  Throughput  : {:.1} req/s", success as f64 / total.as_secs_f64());
    println!("  p50 latency : {}ms", p(50.0));
    println!("  p95 latency : {}ms", p(95.0));
    println!("  p99 latency : {}ms", p(99.0));
    println!("────────────────────────────────────────────────");

    Ok(())
}
