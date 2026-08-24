use anyhow::{Context, Result};

#[derive(Debug, Clone)]
pub struct Config {
    pub database_url:          String,
    pub openrouter_api_key:    String,
    pub openrouter_model:      String,
    pub listen_addr:           String,
    /// Bounded mpsc channel depth (backpressure on uploads)
    pub channel_capacity:      usize,
    /// Tokio worker tasks
    pub worker_count:          usize,
    /// Max concurrent OpenRouter calls (Semaphore permits)
    pub llm_concurrency:       usize,
    /// Consecutive failures before circuit opens
    pub cb_failure_threshold:  u32,
    /// Seconds circuit stays open before half-open probe
    pub cb_reset_timeout_secs: u64,
    /// Optional OTLP collector endpoint (e.g. "http://localhost:4317")
    pub otlp_endpoint:         Option<String>,
}

impl Config {
    pub fn from_env() -> Result<Self> {
        dotenvy::dotenv().ok();
        Ok(Self {
            database_url: env("DATABASE_URL",
                "postgresql://lpc_user:lpc_pass@localhost:5432/lpc_db")?,
            openrouter_api_key: std::env::var("OPENROUTER_API_KEY")
                .context("OPENROUTER_API_KEY must be set")?,
            openrouter_model: env("OPENROUTER_MODEL",
                "nvidia/nemotron-nano-12b-v2-vl:free")?,
            listen_addr:           env("LISTEN_ADDR",       "0.0.0.0:9000")?,
            channel_capacity:      env_parse("CHANNEL_CAPACITY", 32)?,
            worker_count:          env_parse("WORKER_COUNT",     4)?,
            llm_concurrency:       env_parse("LLM_CONCURRENCY",  5)?,
            cb_failure_threshold:  env_parse("CB_FAIL_THRESHOLD", 5)?,
            cb_reset_timeout_secs: env_parse("CB_RESET_SECS",    30)?,
            otlp_endpoint:         std::env::var("OTLP_ENDPOINT").ok(),
        })
    }
}

fn env(key: &str, default: &str) -> Result<String> {
    Ok(std::env::var(key).unwrap_or_else(|_| default.to_owned()))
}

fn env_parse<T: std::str::FromStr>(key: &str, default: T) -> Result<T>
where
    T::Err: std::fmt::Display,
{
    match std::env::var(key) {
        Ok(v) => v.parse::<T>().map_err(|e| anyhow::anyhow!("{key}={v}: {e}")),
        Err(_) => Ok(default),
    }
}
