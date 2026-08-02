"""
Celery tasks — async PDF extraction.
Each task: download PDF → OCR → save to PostgreSQL → push to Google Sheets.
"""
import os
import sys
import pathlib
import tempfile
import uuid
from datetime import datetime

# Ensure project root on sys.path for forked celery workers
_ROOT = str(pathlib.Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from celery import shared_task
from dotenv import load_dotenv

load_dotenv()

from worker.celery_app import celery
from api.database import SessionLocal
from api.models import UploadJob, LPCRow

BACKEND = os.environ.get("MODEL_BACKEND", "openrouter")


def _get_extractor():
    if BACKEND == "gemini":
        from extractor.gemini_extractor import GeminiLPCExtractor
        return GeminiLPCExtractor()
    elif BACKEND == "openrouter":
        from extractor.openrouter_extractor import OpenRouterLPCExtractor
        return OpenRouterLPCExtractor()
    elif BACKEND == "ollama":
        from extractor.ollama_extractor import OllamaLPCExtractor
        return OllamaLPCExtractor()
    raise ValueError(f"Unknown backend: {BACKEND}")


@celery.task(bind=True, max_retries=3, default_retry_delay=30)
def extract_pdf_task(self, job_id: str, pdf_bytes: bytes, filename: str, branch: str,
                     student_name: str = "", class_sec: str = "", roll_no: str = ""):
    """
    Extract LPC data from a PDF and persist to DB + Google Sheets.
    Called by FastAPI after upload.
    """
    db = SessionLocal()
    try:
        job = db.query(UploadJob).filter(UploadJob.id == job_id).first()
        if not job:
            return

        job.status = "processing"
        db.commit()

        # write bytes to temp file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            extractor = _get_extractor()
            record = extractor.extract_pdf(tmp_path)
            record.source_pdf = filename
            record.extraction_notes = f"[branch:{branch}]"
            # manual entry overrides AI extraction for student metadata
            if student_name:
                record.student_name = student_name
            if class_sec:
                record.class_sec = class_sec
            if roll_no:
                record.roll_no = roll_no
        finally:
            os.unlink(tmp_path)

        # persist rows to PostgreSQL
        sig = lambda b: b if b is not None else None
        rows_saved = 0
        for month_entry in record.months:
            for domain, d in month_entry.domains.items():
                row = LPCRow(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    branch=branch,
                    stage=record.stage,
                    student_name=record.student_name,
                    class_sec=record.class_sec,
                    roll_no=record.roll_no,
                    month=month_entry.month,
                    domain=domain,
                    c1=d.c1, c2=d.c2, c3=d.c3, c4=d.c4,
                    observational_anecdote=d.observational_anecdote,
                    strengths=d.strengths,
                    focus_next_month=d.focus_next_month,
                    parent_sign=record.parent_sign_present,
                    teacher_sign=record.teacher_sign_present,
                    principal_sign=record.principal_sign_present,
                    source_pdf=filename,
                )
                db.add(row)
                rows_saved += 1
        db.commit()

        # push to Google Sheets (non-fatal if fails)
        try:
            from extractor.sheets_writer import push_records
            push_records([record])
        except Exception as e:
            print(f"[Sheets] push failed (non-fatal): {e}")

        job.status = "done"
        job.completed_at = datetime.utcnow()
        db.commit()

        return {"status": "done", "rows": rows_saved, "stage": record.stage}

    except Exception as exc:
        db.rollback()
        job = db.query(UploadJob).filter(UploadJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(exc)
            db.commit()
        raise self.retry(exc=exc)
    finally:
        db.close()
