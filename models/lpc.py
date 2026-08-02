from pydantic import BaseModel, Field
from typing import Optional, Literal

LPCStage = Literal["early_years", "foundational_primary", "preparatory", "middle"]
Score = Optional[int]  # 0, 1, 2, or 3

MONTHS = [
    "April", "May", "July", "August", "September",
    "October", "November", "December", "January", "February"
]

DOMAINS_BY_STAGE = {
    "early_years": [
        "Early Literacy", "Hindi", "Early Numeracy",
        "General Awareness", "Socio Emotional"
    ],
    "foundational_primary": [
        "English", "Hindi", "Math", "EVS",
        "Computer/STEM", "Public Speaking"
    ],
    "preparatory": [
        "English", "Hindi", "Math", "Science/EVS",
        "Social Science", "Computer/STEM", "Public Speaking"
    ],
    "middle": [
        "English", "Hindi", "Math", "Science",
        "Social Science", "Computer/STEM", "Public Speaking"
    ],
}


class DomainEntry(BaseModel):
    c1: Score = None   # Competency 1 score (0-3)
    c2: Score = None   # Competency 2 score (0-3)
    c3: Score = None   # Competency 3 score (0-3)
    c4: Score = None   # Competency 4 score (0-3)
    observational_anecdote: Optional[str] = None
    strengths: Optional[str] = None
    focus_next_month: Optional[str] = None


class MonthEntry(BaseModel):
    month: str
    domains: dict[str, DomainEntry] = Field(default_factory=dict)


class LPCRecord(BaseModel):
    """One student's full Learning Progress Card."""
    stage: LPCStage
    student_name: Optional[str] = None
    class_sec: Optional[str] = None
    roll_no: Optional[str] = None
    parent_sign_present: Optional[bool] = None
    teacher_sign_present: Optional[bool] = None
    principal_sign_present: Optional[bool] = None
    months: list[MonthEntry] = Field(default_factory=list)
    extraction_notes: Optional[str] = None

    # source tracking
    source_pdf: Optional[str] = None
