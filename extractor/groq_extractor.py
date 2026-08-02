"""
Groq backend using Llama 4 Scout vision — free tier, works in India.
PDF → images per page → Groq vision API → JSON

Get free API key: https://console.groq.com
Free tier: 30 req/min, 6000 req/day — sufficient for LPC batches.
"""
import base64
import json
import os
import pathlib
import time
from io import BytesIO
from typing import Optional

from groq import Groq
from pdf2image import convert_from_path
from PIL import Image

from models.lpc import LPCRecord, DOMAINS_BY_STAGE

PROMPT_PATH = pathlib.Path(__file__).parent.parent / "prompts" / "lpc_prompt.txt"
PROMPT = PROMPT_PATH.read_text()

DEFAULT_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
MAX_IMAGE_WIDTH = 1600
DPI = 200


def _detect_stage(pdf_path: str) -> Optional[str]:
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


def _pdf_to_b64_images(pdf_path: str, dpi: int = DPI) -> list[str]:
    images = convert_from_path(pdf_path, dpi=dpi)
    result = []
    for img in images:
        w, h = img.size
        if w > MAX_IMAGE_WIDTH:
            img = img.resize((MAX_IMAGE_WIDTH, int(h * MAX_IMAGE_WIDTH / w)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        result.append(base64.b64encode(buf.getvalue()).decode())
    return result


def _fill_missing_domains(data: dict) -> dict:
    if "stage" in data and "months" in data:
        stage = data["stage"]
        expected = DOMAINS_BY_STAGE.get(stage, [])
        for month_entry in data["months"]:
            for domain in expected:
                month_entry.setdefault("domains", {}).setdefault(domain, {
                    "c1": None, "c2": None, "c3": None, "c4": None,
                    "observational_anecdote": None,
                    "strengths": None,
                    "focus_next_month": None,
                })
    return data


class GroqLPCExtractor:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise ValueError("GROQ_API_KEY not set. Get free key at console.groq.com")
        self.client = Groq(api_key=key)
        self.model = model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL)

    def extract_pdf(self, pdf_path: str, retry: int = 3) -> LPCRecord:
        pdf_path = str(pdf_path)
        stage_hint = _detect_stage(pdf_path)
        hint = f"\n\nHINT: This PDF is likely stage='{stage_hint}'" if stage_hint else ""
        prompt = PROMPT + hint

        print(f"  Converting PDF to images ...")
        images_b64 = _pdf_to_b64_images(pdf_path)
        print(f"  {len(images_b64)} page(s) → sending to Groq ({self.model}) ...")

        # Build content: all page images + prompt text
        content = []
        for b64 in images_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        content.append({"type": "text", "text": prompt})

        for attempt in range(retry + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": content}],
                    temperature=0.1,
                    max_tokens=8192,
                    response_format={"type": "json_object"},
                )
                raw = response.choices[0].message.content.strip()

                # strip markdown fences if model wraps anyway
                if raw.startswith("```"):
                    parts = raw.split("```")
                    raw = parts[1]
                    if raw.startswith("json"):
                        raw = raw[4:]

                data = json.loads(raw)
                data = _fill_missing_domains(data)

                record = LPCRecord(**data)
                record.source_pdf = pdf_path
                return record

            except Exception as e:
                err_str = str(e)
                if attempt < retry:
                    wait = 5
                    if "429" in err_str or "rate_limit" in err_str.lower():
                        wait = 65  # Groq rate limit resets per minute
                        print(f"  Rate limit — waiting {wait}s ...")
                    else:
                        print(f"  Retry {attempt + 1} after error: {e}")
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"Groq extraction failed for {pdf_path}: {e}") from e
