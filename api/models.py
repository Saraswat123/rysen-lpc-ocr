"""SQLAlchemy ORM models for LPC data."""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, Enum, Float
from sqlalchemy.dialects.postgresql import UUID

from api.database import Base

JOB_STATUS = ("pending", "processing", "done", "failed")


class UploadJob(Base):
    """One PDF upload = one job."""
    __tablename__ = "upload_jobs"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    branch       = Column(String(120), nullable=False)
    filename     = Column(String(255), nullable=False)
    status       = Column(Enum(*JOB_STATUS, name="job_status"), default="pending")
    error        = Column(Text, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class LPCRow(Base):
    """One flat row per student × month × domain."""
    __tablename__ = "lpc_rows"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id               = Column(UUID(as_uuid=True), nullable=False, index=True)
    branch               = Column(String(120), nullable=False, index=True)
    stage                = Column(String(50), nullable=False, index=True)
    student_name         = Column(String(150))
    class_sec            = Column(String(50))
    roll_no              = Column(String(30))
    month                = Column(String(20))
    domain               = Column(String(80))
    c1                   = Column(Integer, nullable=True)
    c2                   = Column(Integer, nullable=True)
    c3                   = Column(Integer, nullable=True)
    c4                   = Column(Integer, nullable=True)
    observational_anecdote = Column(Text, nullable=True)
    strengths            = Column(Text, nullable=True)
    focus_next_month     = Column(Text, nullable=True)
    parent_sign          = Column(Boolean, nullable=True)
    teacher_sign         = Column(Boolean, nullable=True)
    principal_sign       = Column(Boolean, nullable=True)
    source_pdf           = Column(String(255))
    created_at           = Column(DateTime, default=datetime.utcnow)
