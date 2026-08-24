use std::sync::Arc;
use std::sync::atomic::{AtomicU32, AtomicU64, AtomicU8, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

const CLOSED:    u8 = 0;
const OPEN:      u8 = 1;
const HALF_OPEN: u8 = 2;

#[derive(thiserror::Error, Debug)]
pub enum CbError {
    #[error("circuit OPEN — OpenRouter suspended, retry in {0}ms")]
    Open(u64),
}

/// Lock-free circuit breaker using atomics.
///
/// State machine:
///   Closed ──(failures >= threshold)──> Open
///   Open   ──(reset_timeout elapsed)──> HalfOpen
///   HalfOpen ──(success)──> Closed
///   HalfOpen ──(failure)──> Open
pub struct CircuitBreaker {
    state:             AtomicU8,
    failures:          AtomicU32,
    last_failure_ms:   AtomicU64,
    failure_threshold: u32,
    reset_timeout_ms:  u64,
}

impl CircuitBreaker {
    pub fn new(failure_threshold: u32, reset_timeout_secs: u64) -> Arc<Self> {
        Arc::new(Self {
            state:             AtomicU8::new(CLOSED),
            failures:          AtomicU32::new(0),
            last_failure_ms:   AtomicU64::new(0),
            failure_threshold,
            reset_timeout_ms: reset_timeout_secs * 1_000,
        })
    }

    /// Returns Ok if a call may proceed, Err(CbError::Open) if blocked.
    pub fn check(&self) -> Result<(), CbError> {
        match self.state.load(Ordering::Acquire) {
            CLOSED => Ok(()),
            HALF_OPEN => Ok(()), // probe allowed through
            OPEN => {
                let elapsed = now_ms().saturating_sub(self.last_failure_ms.load(Ordering::Relaxed));
                if elapsed >= self.reset_timeout_ms {
                    // Attempt transition → HalfOpen; if another thread beat us, still ok
                    let _ = self.state.compare_exchange(
                        OPEN, HALF_OPEN, Ordering::AcqRel, Ordering::Relaxed,
                    );
                    Ok(())
                } else {
                    Err(CbError::Open(self.reset_timeout_ms - elapsed))
                }
            }
            _ => Ok(()),
        }
    }

    pub fn record_success(&self) {
        if self.state.load(Ordering::Acquire) == HALF_OPEN {
            self.state.store(CLOSED, Ordering::Release);
            tracing::info!("circuit breaker → CLOSED (probe succeeded)");
        }
        self.failures.store(0, Ordering::Relaxed);
    }

    pub fn record_failure(&self) {
        self.last_failure_ms.store(now_ms(), Ordering::Relaxed);
        let after = self.failures.fetch_add(1, Ordering::AcqRel) + 1;

        if after >= self.failure_threshold || self.state.load(Ordering::Acquire) == HALF_OPEN {
            self.state.store(OPEN, Ordering::Release);
            tracing::warn!(
                failures = after,
                threshold = self.failure_threshold,
                "circuit breaker → OPEN (OpenRouter calls suspended)"
            );
        }
    }

    pub fn state_str(&self) -> &'static str {
        match self.state.load(Ordering::Relaxed) {
            CLOSED    => "closed",
            OPEN      => "open",
            HALF_OPEN => "half_open",
            _         => "unknown",
        }
    }

    pub fn failure_count(&self) -> u32 {
        self.failures.load(Ordering::Relaxed)
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
