"""
LPC OCR — FastAPI backend.
Handles upload, queuing, and job status.
Dashboard (Streamlit) talks to this API.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from api.database import engine, Base
import api.models  # register models

Base.metadata.create_all(bind=engine)

from api.routes.upload import router as upload_router

app = FastAPI(
    title="RYSEN LPC OCR API",
    description="Upload LPC PDFs → AI extraction → PostgreSQL + Google Sheets",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "RYSEN LPC OCR API v2"}
