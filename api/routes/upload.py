"""Upload endpoint — dispatches to Celery (Python) or Rust async worker."""
import os
import uuid
from typing import List

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import UploadJob

router = APIRouter(prefix="/api", tags=["upload"])

ALLOWED_BRANCHES = [
    "Beawar", "Beawar NLC 1", "Beawar NLC 2",
    "Bikaner - Virat Nagar", "Bikaner - Vyas Colony",
    "Nimbahera - Main", "Nimbahera - Preschool",
    "Deoli - Main", "Deoli - Preschool",
    "Jaisalmer",
    "Sri Ganganagar - Main", "Sri Ganganagar - Preschool",
    "Sri Vijaynagar", "Pilibanga",
    "Udaipur",
]

# Set WORKER_BACKEND=rust in .env to route to the Rust async worker.
# Default: celery (existing Python worker).
WORKER_BACKEND  = os.environ.get("WORKER_BACKEND", "celery")
RUST_WORKER_URL = os.environ.get("RUST_WORKER_URL", "http://localhost:9000")


async def _dispatch_rust(
    job_id: str, pdf_bytes: bytes, filename: str,
    branch: str, student_name: str, class_sec: str, roll_no: str,
) -> None:
    """POST job to Rust worker's /submit endpoint."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{RUST_WORKER_URL}/submit",
            data={
                "job_id":       job_id,
                "branch":       branch,
                "student_name": student_name,
                "class_sec":    class_sec,
                "roll_no":      roll_no,
            },
            files={"file": (filename, pdf_bytes, "application/pdf")},
        )
    if resp.status_code == 503:
        raise HTTPException(503, f"Rust worker queue full — try again shortly")
    resp.raise_for_status()


def _dispatch_celery(
    job_id: str, pdf_bytes: bytes, filename: str,
    branch: str, student_name: str, class_sec: str, roll_no: str,
) -> None:
    from worker.tasks import extract_pdf_task
    extract_pdf_task.apply_async(
        args=[job_id, pdf_bytes, filename, branch, student_name, class_sec, roll_no],
        task_id=job_id,
    )


@router.post("/upload")
async def upload_pdfs(
    branch:       str = Form(...),
    student_name: str = Form(""),
    class_sec:    str = Form(""),
    roll_no:      str = Form(""),
    files:        List[UploadFile] = File(...),
    db:           Session = Depends(get_db),
):
    if branch not in ALLOWED_BRANCHES:
        raise HTTPException(400, f"Unknown branch: {branch}")

    jobs = []
    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, f"Only PDFs allowed: {file.filename}")

        pdf_bytes = await file.read()
        if len(pdf_bytes) > 50 * 1024 * 1024:
            raise HTTPException(400, f"PDF too large (max 50MB): {file.filename}")

        job_id = str(uuid.uuid4())
        job = UploadJob(id=job_id, branch=branch, filename=file.filename, status="pending")
        db.add(job)
        db.commit()

        if WORKER_BACKEND == "rust":
            await _dispatch_rust(
                job_id, pdf_bytes, file.filename,
                branch, student_name, class_sec, roll_no,
            )
        else:
            _dispatch_celery(
                job_id, pdf_bytes, file.filename,
                branch, student_name, class_sec, roll_no,
            )

        jobs.append({"job_id": job_id, "filename": file.filename, "backend": WORKER_BACKEND})

    return JSONResponse({"queued": len(jobs), "jobs": jobs})


@router.get("/jobs/{job_id}")
def job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.query(UploadJob).filter(UploadJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "id":           str(job.id),
        "branch":       job.branch,
        "filename":     job.filename,
        "status":       job.status,
        "error":        job.error,
        "created_at":   str(job.created_at),
        "completed_at": str(job.completed_at) if job.completed_at else None,
    }


@router.get("/jobs")
def list_jobs(branch: str = None, limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(UploadJob)
    if branch:
        q = q.filter(UploadJob.branch == branch)
    jobs = q.order_by(UploadJob.created_at.desc()).limit(limit).all()
    return [
        {"id": str(j.id), "filename": j.filename, "branch": j.branch,
         "status": j.status, "created_at": str(j.created_at)}
        for j in jobs
    ]
