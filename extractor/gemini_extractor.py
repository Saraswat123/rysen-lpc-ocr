"""
Gemini 2.0 Flash extractor — new google.genai SDK.
Free tier: 1,500 req/day, 1M tokens/day.
"""
import json
import os
import pathlib
import time
from typing import Optional

from google import genai
from google.genai import types

from models.lpc import LPCRecord, DOMAINS_BY_STAGE

PROMPT_PATH = pathlib.Path(__file__).parent.parent / "prompts" / "lpc_prompt.txt"
PROMPT = PROMPT_PATH.read_text()


def _detect_stage_from_filename(pdf_path: str) -> Optional[str]:
    name = pathlib.Path(pdf_path).stem.lower()
    if "early" in name:
        return "early_years"
    if "foundational" in name or "primary" in name:
        return "foundational_primary"
    if "preparatory" in name:
        return "preparatory"
    if "middle" in name:
        return "middle"
    return None


def _build_prompt(stage_hint: Optional[str]) -> str:
    hint = f"\n\nHINT: This PDF is likely stage='{stage_hint}'" if stage_hint else ""
    return PROMPT + hint


class GeminiLPCExtractor:
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY not set. Get free key at aistudio.google.com")
        self.client = genai.Client(api_key=key)

    def extract_pdf(self, pdf_path: str, retry: int = 2) -> LPCRecord:
        """Upload PDF to Gemini, extract structured LPC data, return LPCRecord."""
        pdf_path = str(pdf_path)
        stage_hint = _detect_stage_from_filename(pdf_path)
        prompt = _build_prompt(stage_hint)

        print(f"  Uploading {pathlib.Path(pdf_path).name} ...")
        with open(pdf_path, "rb") as f:
            uploaded = self.client.files.upload(
                file=f,
                config=types.UploadFileConfig(mime_type="application/pdf"),
            )

        for attempt in range(retry + 1):
            try:
                print(f"  Extracting (attempt {attempt + 1}) ...")
                model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=[uploaded, prompt],
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        response_mime_type="application/json",
                    ),
                )
                raw = response.text.strip()

                # strip markdown fences if model wraps anyway
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]

                data = json.loads(raw)

                # fill domains if model skipped any
                if "stage" in data and "months" in data:
                    stage = data["stage"]
                    expected_domains = DOMAINS_BY_STAGE.get(stage, [])
                    for month_entry in data["months"]:
                        for domain in expected_domains:
                            if domain not in month_entry.get("domains", {}):
                                month_entry.setdefault("domains", {})[domain] = {
                                    "c1": None, "c2": None,
                                    "c3": None, "c4": None,
                                    "observational_anecdote": None,
                                    "strengths": None,
                                    "focus_next_month": None,
                                }

                record = LPCRecord(**data)
                record.source_pdf = pdf_path

                # cleanup uploaded file
                try:
                    self.client.files.delete(name=uploaded.name)
                except Exception:
                    pass

                return record

            except Exception as e:
                err_str = str(e)
                if attempt < retry:
                    # parse retryDelay from 429 response (API tells us exactly how long to wait)
                    wait = 2
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        import re
                        m = re.search(r"retryDelay.*?(\d+)s", err_str)
                        if m:
                            wait = int(m.group(1)) + 5  # add 5s buffer
                        else:
                            wait = 60  # default for quota errors
                        print(f"  Quota hit — waiting {wait}s before retry ...")
                    else:
                        print(f"  Retry after error: {e}")
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"Extraction failed for {pdf_path}: {e}") from e
