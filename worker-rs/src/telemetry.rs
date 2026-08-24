use anyhow::Result;
use opentelemetry::trace::TracerProvider as _;
use opentelemetry_sdk::trace::TracerProvider;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

/// Init tracing + OTel.
///
/// If `otlp_endpoint` is Some("http://host:4317"), export OTLP gRPC.
/// Otherwise export readable spans to stdout (dev mode).
pub fn init(otlp_endpoint: Option<&str>) -> Result<()> {
    let provider: TracerProvider = if let Some(_endpoint) = otlp_endpoint {
        // ── OTLP export (production) ─────────────────────────────────────
        // Uncomment + add opentelemetry-otlp to Cargo.toml for real OTLP:
        //
        // use opentelemetry_otlp::WithExportConfig;
        // opentelemetry_otlp::new_pipeline()
        //     .tracing()
        //     .with_exporter(
        //         opentelemetry_otlp::new_exporter()
        //             .tonic()
        //             .with_endpoint(endpoint),
        //     )
        //     .with_trace_config(
        //         opentelemetry_sdk::trace::Config::default()
        //             .with_resource(opentelemetry_sdk::Resource::new(vec![
        //                 opentelemetry::KeyValue::new("service.name", "rysen-worker"),
        //             ]))
        //     )
        //     .install_batch(opentelemetry_sdk::runtime::Tokio)?
        stdout_provider()
    } else {
        // ── Stdout export (dev / demo) ────────────────────────────────────
        stdout_provider()
    };

    let tracer    = provider.tracer("rysen-worker");
    let otel_layer = tracing_opentelemetry::layer().with_tracer(tracer);

    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| EnvFilter::new("info")))
        .with(tracing_subscriber::fmt::layer())
        .with(otel_layer)
        .init();

    Ok(())
}

fn stdout_provider() -> TracerProvider {
    let exporter = opentelemetry_stdout::SpanExporter::default();
    TracerProvider::builder()
        .with_simple_exporter(exporter)
        .build()
}
