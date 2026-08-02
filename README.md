# RYSEN LPC OCR Pipeline

Automated extraction system for handwritten **Learning Progress Card (LPC)** forms across RYSEN Group of Schools — 15 branch locations in Rajasthan.

Uploads a scanned PDF → AI vision model reads the handwriting → data saved to PostgreSQL → auto-pushed to branch-specific Google Sheet.

---

## What it does

- **Upload** scanned LPC PDFs through a web dashboard (branch + student details)
- **Extract** scores, observations, and signatures using an AI vision model (OpenRouter)
- **Store** all data permanently in PostgreSQL
- **Auto-push** to the correct Google Sheet for each of 15 branch locations
- **Scale** to 10,000+ PDFs/month via async Celery queue (4 parallel workers)

---

## Architecture

```
[Streamlit Dashboard :8501]
        │  PDF + student details
        ▼
[FastAPI :8000]  →  creates UploadJob in PostgreSQL
        │
        ▼
[Redis :6379]  ←→  [Celery Workers ×4]
                          │
                    ┌─────┴──────────────────┐
                    │                        │
              [OpenRouter Vision]     [pdf2image + Pillow]
              nvidia/nemotron-nano    PDF → JPEG → base64
                    │
                    ▼
              [PostgreSQL :5432]  ←  lpc_rows + upload_jobs
                    │
                    ▼
        [Google Sheets — 1 sheet per branch]
         Early Years | Foundational | Preparatory | Middle
```

---

## LPC Stages

| Stage | Classes | Key |
|---|---|---|
| Early Years | Nursery – KG | `early_years` |
| Foundational Primary | Grade 1–2 | `foundational_primary` |
| Preparatory | Grade 3–5 | `preparatory` |
| Middle | Grade 6–8 | `middle` |

**Score scale (C1–C4):** `0` = Not observed · `1` = Needs significant support · `2` = At grade level, needs support · `3` = Independent

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
| Queue | Celery 5 + Redis 7 |
| OCR | OpenRouter (`nvidia/nemotron-nano-12b-v2-vl:free`) |
| PDF→Image | pdf2image + Pillow |
| Data validation | Pydantic v2 |
| Database | PostgreSQL 15 via SQLAlchemy |
| Sheets | gspread + Google OAuth2 |
| Excel export | openpyxl |
| Containers | Docker Compose (Postgres + Redis) |

---

## Project Structure

```
ocr_extractor/
├── dashboard.py                 # Streamlit upload UI
├── main.py                      # Standalone CLI extractor (dev/test)
├── setup_sheets.py              # One-time: creates 15 Google Sheets
├── requirements.txt
├── docker-compose.yml           # PostgreSQL + Redis
├── .env.example                 # Environment variable template
│
├── api/
│   ├── main.py                  # FastAPI app, startup, CORS
│   ├── database.py              # SQLAlchemy engine + session
│   ├── models.py                # UploadJob + LPCRow ORM models
│   └── routes/
│       └── upload.py            # POST /api/upload, GET /api/jobs
│
├── worker/
│   ├── celery_app.py            # Celery config, Redis broker
│   └── tasks.py                 # extract_pdf_task (OCR → DB → Sheets)
│
├── extractor/
│   ├── openrouter_extractor.py  # Active: OpenRouter vision model
│   ├── gemini_extractor.py      # Future: Gemini 2.0 Flash
│   ├── groq_extractor.py        # Fallback: Groq (no vision currently)
│   ├── ollama_extractor.py      # Local offline fallback
│   ├── sheets_writer.py         # Google Sheets push (branch routing)
│   └── excel_writer.py          # RYSEN-branded Excel export
│
├── models/
│   └── lpc.py                   # Pydantic models: LPCRecord, MonthEntry, DomainEntry
│
└── prompts/
    └── lpc_prompt.txt           # AI extraction prompt (stage detection, domains, scoring)
```

---

## Setup

### 1. Prerequisites

```bash
brew install poppler          # required by pdf2image
```

Docker Desktop must be running.

### 2. Clone and install

```bash
git clone https://github.com/Saraswat123/rysen-lpc-ocr.git
cd rysen-lpc-ocr
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```env
MODEL_BACKEND=openrouter
OPENROUTER_API_KEY=your_key_here        # https://openrouter.ai/keys
OPENROUTER_MODEL=nvidia/nemotron-nano-12b-v2-vl:free
GOOGLE_OAUTH_CLIENT=/path/to/oauth_client.json
```

### 4. Google OAuth credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project → enable **Google Sheets API** + **Google Drive API**
3. Create credentials → **OAuth 2.0 Desktop app** → download JSON
4. Save as `oauth_client.json` (path must match `GOOGLE_OAUTH_CLIENT` in `.env`)

### 5. Start infrastructure

```bash
docker compose up -d
```

### 6. Create Google Sheets (one-time)

```bash
python setup_sheets.py
```

This creates 15 sheets (one per branch), each with 4 stage tabs and headers. A browser window opens for Google login on first run. Saves `sheets_config.json` locally (not committed).

### 7. Start all services

**Terminal 1 — FastAPI:**
```bash
source venv/bin/activate
uvicorn api.main:app --reload --port 8000
```

**Terminal 2 — Celery workers:**
```bash
source venv/bin/activate
PYTHONPATH=$(pwd) celery -A worker.celery_app worker --loglevel=info --concurrency=4
```

**Terminal 3 — Dashboard:**
```bash
source venv/bin/activate
streamlit run dashboard.py
```

Open [http://localhost:8501](http://localhost:8501)

---

## Usage

1. **Select branch** from the dropdown (15 locations)
2. **Enter student details** — Name, Class, Roll No., Section
3. **Upload LPC PDF** (scanned form, any stage)
4. Click **Upload & Extract**
5. Watch live status — done in ~2 minutes per PDF
6. Click the Google Sheet link to see extracted data

---

## Database Schema

**`upload_jobs`**

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | job identifier |
| branch | varchar | one of 15 branch names |
| filename | varchar | original PDF name |
| status | varchar | pending / processing / done / failed |
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
| domain | varchar | subject/skill area |
| c1 – c4 | int (0–3) | score per criteria, nullable |
| observational_anecdote | text | |
| strengths | text | |
| focus_next_month | text | |
| parent_sign | boolean | signature present? |
| teacher_sign | boolean | |
| principal_sign | boolean | |
| source_pdf | varchar | original filename |
| created_at | timestamp | |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/upload` | Upload PDF(s), returns job_id(s) |
| GET | `/api/jobs/{job_id}` | Get job status + error |
| GET | `/api/jobs?branch=X` | List recent jobs, filterable by branch |

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `MODEL_BACKEND` | Yes | `openrouter` / `gemini` / `ollama` |
| `OPENROUTER_API_KEY` | If openrouter | Get from openrouter.ai/keys |
| `OPENROUTER_MODEL` | No | Defaults to `nvidia/nemotron-nano-12b-v2-vl:free` |
| `GEMINI_API_KEY` | If gemini | Get from aistudio.google.com |
| `GOOGLE_OAUTH_CLIENT` | Yes | Path to OAuth2 Desktop credentials JSON |
| `DATABASE_URL` | No | Defaults to Docker Postgres |
| `REDIS_URL` | No | Defaults to Docker Redis |

---

## Files NOT in this repo

These files contain secrets and must be set up locally:

| File | Why excluded | How to get |
|---|---|---|
| `.env` | Contains API keys | Copy `.env.example`, fill in values |
| `oauth_client.json` | Google OAuth secret | Download from Google Cloud Console |
| `oauth_token.json` | Live auth token | Auto-generated by `setup_sheets.py` |
| `sheets_config.json` | Sheet IDs for 15 branches | Auto-generated by `setup_sheets.py` |

---

## Switching OCR Backend

Change `MODEL_BACKEND` in `.env`:

```env
MODEL_BACKEND=openrouter   # free, active
MODEL_BACKEND=gemini       # better accuracy, needs billing
MODEL_BACKEND=ollama       # fully local/offline
```

No code changes needed — the worker picks up the env var at startup.

---

## License

Private — RYSEN Group of Schools / AITS internal tooling.
