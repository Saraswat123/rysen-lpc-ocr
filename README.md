# RYSEN LPC OCR Pipeline

Automated extraction system for handwritten **Learning Progress Card (LPC)** forms across RYSEN Group of Schools — 15 branch locations in Rajasthan.

Scanned PDF → AI vision model reads handwriting → PostgreSQL → Google Sheet per branch.

---

## What it does

- **Upload** scanned LPC PDFs via web dashboard (branch + student details)
- **Extract** scores, observations, and signatures using OpenRouter vision model
- **Store** all data permanently in PostgreSQL
- **Auto-push** to correct Google Sheet for each of 15 branch locations
- **Scale** to 10,000+ PDFs/month via Rust async worker (4 tokio workers, bounded channel)

---

## Architecture

```
[Streamlit Dashboard :8501]
        │  PDF + student details
        ▼
[FastAPI :8000]  →  creates UploadJob in PostgreSQL
        │
        ├─── WORKER_BACKEND=rust (default) ──────────────────────────┐
        │                                                             │
        │    [Rust Worker :9000]                                      │
        │    axum HTTP server                                         │
        │    mpsc channel (cap 32) → backpressure 503                │
        │    tokio workers ×4                                         │
        │    Semaphore (max 5 concurrent LLM calls)                  │
        │    Circuit breaker (5 fail → OPEN 30s, lock-free atomic)   │
        │    OTel spans → stdout / OTLP                              │
        │                                                             │
        └─── WORKER_BACKEND=celery (fallback) ───────────────────────┤
                                                                      │
             [Redis :6379] ←→ [Celery Workers ×4]                    │
                                                                      │
        ┌─────────────────────────────────────────────────────────────┘
        │
        ▼
[pdftoppm]  PDF → JPEG pages (poppler, 150 DPI)
        │
        ▼
[OpenRouter Vision API]  nvidia/nemotron-nano-12b-v2-vl:free
  → structured JSON: stage, months, domains, scores (C1–C4)
        │
        ▼
[PostgreSQL]  lpc_rows + upload_jobs (sqlx / SQLAlchemy)
        │
        ▼
[Google Sheets]  1 sheet per branch × 4 stage tabs
```

---

## LPC Stages

| Stage | Classes | Key |
|---|---|---|
| Early Years | Nursery – KG | `early_years` |
| Foundational Primary | Grade 1–2 | `foundational_primary` |
| Preparatory | Grade 3–5 | `preparatory` |
| Middle | Grade 6–8 | `middle` |

**Score scale (C1–C4):** `0` Not observed · `1` Needs significant support · `2` At grade level, needs support · `3` Independent

**10 academic months:** April · May · July · August · September · October · November · December · January · February

---

## Branch Locations (15)

Beawar · Beawar NLC 1 · Beawar NLC 2 · Bikaner – Virat Nagar · Bikaner – Vyas Colony · Nimbahera – Main · Nimbahera – Preschool · Deoli – Main · Deoli – Preschool · Jaisalmer · Sri Ganganagar – Main · Sri Ganganagar – Preschool · Sri Vijaynagar · Pilibanga · Udaipur

---

## Tech Stack

| Layer | Technology |
|---|---|
| Dashboard | Streamlit |
| API | FastAPI + Uvicorn |
| Rust worker | axum · tokio · sqlx · reqwest |
| Rust resilience | mpsc channel · Semaphore · CancellationToken · lock-free circuit breaker |
| Observability | OpenTelemetry (tracing spans → stdout / OTLP) |
| Python worker | Celery 5 + Redis 7 (fallback path) |
| OCR | OpenRouter (`nvidia/nemotron-nano-12b-v2-vl:free`) |
| PDF → Image | pdftoppm (poppler) |
| Data validation | Pydantic v2 (Python) · serde (Rust) |
| Database | PostgreSQL 15 via sqlx (Rust) / SQLAlchemy (Python) |
| Sheets | gspread + Google OAuth2 |
| Containers | Docker Compose (Postgres + Redis) |

---

## Project Structure

```
rysen-lpc-ocr/
├── dashboard.py                  # Streamlit upload UI
├── main.py                       # Standalone CLI extractor (dev/test)
├── setup_sheets.py               # One-time: create 15 Google Sheets
├── requirements.txt
├── docker-compose.yml            # PostgreSQL + Redis
├── .env.example                  # Environment variable template
│
├── api/
│   ├── main.py                   # FastAPI app, startup, CORS
│   ├── database.py               # SQLAlchemy engine + session
│   ├── models.py                 # UploadJob + LpcRow ORM models
│   └── routes/
│       └── upload.py             # POST /api/upload → Rust or Celery dispatch
│
├── worker/                       # Python Celery worker (fallback)
│   ├── celery_app.py
│   └── tasks.py                  # extract_pdf_task (OCR → DB → Sheets)
│
├── worker-rs/                    # Rust async worker (production)
│   ├── Cargo.toml
│   ├── src/
│   │   ├── main.rs               # axum server :9000  /submit  /health
│   │   ├── worker.rs             # 4× tokio workers, mpsc channel (cap 32)
│   │   ├── extractor.rs          # pdf→jpeg→openrouter→LpcExtraction
│   │   ├── db.rs                 # sqlx: mark_processing/done/failed, insert_lpc_rows
│   │   ├── circuit_breaker.rs    # AtomicU8 state, 5 fail → OPEN, 30s → HalfOpen
│   │   ├── config.rs             # env var config
│   │   └── telemetry.rs          # OTel spans stdout/OTLP
│   └── src/bin/
│       └── load_test.rs          # p50/p95/p99 latency binary
│
├── extractor/                    # Python OCR backends
│   ├── openrouter_extractor.py   # active
│   ├── gemini_extractor.py       # future (better accuracy)
│   ├── groq_extractor.py
│   ├── ollama_extractor.py       # local offline fallback
│   ├── sheets_writer.py          # Google Sheets push, branch routing
│   └── excel_writer.py
│
├── models/
│   └── lpc.py                    # Pydantic: LpcExtraction, MonthData, DomainData
│
└── prompts/
    └── lpc_prompt.txt            # Vision model prompt → strict JSON extraction
```

---

## Setup

### 1. Prerequisites

```bash
brew install poppler              # pdftoppm — PDF to JPEG conversion
brew install rust                 # Rust toolchain (for Rust worker)
```

Docker Desktop must be running (Postgres + Redis).

### 2. Clone and install Python deps

```bash
git clone https://github.com/Saraswat123/rysen-lpc-ocr.git
cd rysen-lpc-ocr
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Build Rust worker

```bash
cd worker-rs
cargo build --release
cd ..
```

### 4. Environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# OCR
OPENROUTER_API_KEY=your_key_here        # https://openrouter.ai/keys
OPENROUTER_MODEL=nvidia/nemotron-nano-12b-v2-vl:free

# Worker backend: rust (production) or celery (fallback)
WORKER_BACKEND=rust
RUST_WORKER_URL=http://localhost:9000

# Database — Docker default, or paste Neon/Supabase URL
DATABASE_URL=postgresql://lpc_user:lpc_pass@localhost:5432/lpc_db

# Google Sheets
GOOGLE_OAUTH_CLIENT=/path/to/oauth_client.json
```

### 5. Google OAuth credentials

1. [console.cloud.google.com](https://console.cloud.google.com) → create project
2. Enable **Google Sheets API** + **Google Drive API**
3. Credentials → **OAuth 2.0 Desktop app** → download JSON
4. Save as `oauth_client.json`, set path in `.env`

### 6. Start infrastructure

```bash
docker compose up -d              # Postgres :5432 + Redis :6379
```

> **No Docker?** Use free cloud Postgres (Neon · Supabase · Railway). Set `DATABASE_URL` to the connection string and skip this step. Redis only needed for Celery path.

### 7. Create Google Sheets (one-time)

```bash
python setup_sheets.py
```

Creates 15 sheets (one per branch), 4 stage tabs each. Browser opens for Google login. Saves `sheets_config.json` locally (gitignored).

### 8. Start all services

**Terminal 1 — Rust worker:**
```bash
cd worker-rs
RUST_LOG=info ./target/release/rysen-worker
```

**Terminal 2 — FastAPI:**
```bash
source venv/bin/activate
uvicorn api.main:app --reload --port 8000
```

**Terminal 3 — Celery (only if WORKER_BACKEND=celery):**
```bash
source venv/bin/activate
PYTHONPATH=$(pwd) celery -A worker.celery_app worker --loglevel=info --concurrency=4
```

**Terminal 4 — Dashboard:**
```bash
source venv/bin/activate
streamlit run dashboard.py
```

Open [http://localhost:8501](http://localhost:8501)

---

## Usage

1. Select **branch** (15 locations)
2. Enter **student details** — Name, Class, Section, Roll No.
3. Upload **LPC PDF** (scanned, any stage)
4. Click **Upload & Extract**
5. Status updates live — ~3 min per PDF on free model
6. Click the Google Sheet link to view extracted data

---

## Rust Worker Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Status, circuit breaker state, failure count, queue capacity |
| POST | `/submit` | Multipart: `job_id`, `branch`, `student_name`, `class_sec`, `roll_no`, `file` |

Returns `503` with `{"error":"queue full"}` when channel at capacity. FastAPI surfaces this as a retryable error.

### Load test

```bash
cd worker-rs
cargo run --bin load-test -- --url http://localhost:9000 --jobs 50 --concurrency 10
```

Reports total / success / errors, throughput req/s, p50 / p95 / p99 latency.

---

## Database Schema

**`upload_jobs`**

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | job identifier |
| branch | varchar | one of 15 branch names |
| filename | varchar | original PDF name |
| status | enum | pending / processing / done / failed |
| error | text | error message if failed |
| created_at | timestamp | |
| completed_at | timestamp | null until done |

**`lpc_rows`**

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| job_id | UUID FK | → upload_jobs |
| branch | varchar | |
| stage | varchar | early_years / foundational_primary / preparatory / middle |
| student_name | varchar | |
| class_sec | varchar | |
| roll_no | varchar | |
| month | varchar | April … February |
| domain | varchar | subject / skill area |
| c1 – c4 | int (0–3) | score per criteria, nullable |
| observational_anecdote | text | |
| strengths | text | |
| focus_next_month | text | |
| parent_sign | boolean | signature present |
| teacher_sign | boolean | |
| principal_sign | boolean | |
| source_pdf | varchar | original filename |
| created_at | timestamp | |

---

## FastAPI Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/upload` | Upload PDF(s), returns job_id(s) |
| GET | `/api/jobs/{job_id}` | Job status + error |
| GET | `/api/jobs?branch=X` | List recent jobs, filter by branch |

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Yes | — | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `OPENROUTER_MODEL` | No | `nvidia/nemotron-nano-12b-v2-vl:free` | Vision model |
| `WORKER_BACKEND` | No | `celery` | `rust` or `celery` |
| `RUST_WORKER_URL` | If rust | `http://localhost:9000` | Rust worker base URL |
| `DATABASE_URL` | No | Docker Postgres | PostgreSQL connection string |
| `REDIS_URL` | If celery | `redis://localhost:6379/0` | Redis broker |
| `GOOGLE_OAUTH_CLIENT` | Yes | — | Path to OAuth2 Desktop JSON |
| `GEMINI_API_KEY` | If gemini | — | [aistudio.google.com](https://aistudio.google.com) |
| `MODEL_BACKEND` | No | `openrouter` | Python extractor backend |

---

## Files NOT in this repo

| File | Why excluded | How to get |
|---|---|---|
| `.env` | API keys | Copy `.env.example`, fill in |
| `oauth_client.json` | Google OAuth secret | Download from Google Cloud Console |
| `oauth_token.json` | Live auth token | Auto-generated by `setup_sheets.py` |
| `sheets_config.json` | Sheet IDs for 15 branches | Auto-generated by `setup_sheets.py` |

---

## Switching OCR Backend (Python path)

```env
MODEL_BACKEND=openrouter   # free, active (default)
MODEL_BACKEND=gemini       # better accuracy, needs billing
MODEL_BACKEND=ollama       # fully local / offline
```

No code changes needed — worker picks up env var at startup.

---

## License

Private — RYSEN Group of Schools / AITS internal tooling.
