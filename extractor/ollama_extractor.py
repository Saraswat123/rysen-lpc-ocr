"""
Local Ollama backend using Qwen2.5-VL — runs on M4 16GB, fully offline.
Needs: ollama pull qwen2.5vl (or qwen2-vl:7b)
PDF → images per page → Ollama vision → JSON

Install Ollama: brew install ollama
Pull model:     ollama pull qwen2.5vl
"""
import base64
import json
import pathlib
import time
from io import BytesIO
from typing import Optional

import requests
from pdf2image import convert_from_path
from PIL import Image

from models.lpc import LPCRecord, DOMAINS_BY_STAGE

PROMPT_PATH = pathlib.Path(__file__).parent.parent / "prompts" / "lpc_prompt.txt"
PROMPT = PROMPT_PATH.read_text()

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5vl"          # or "qwen2-vl:7b" depending on Ollama version

STAGE_KEYWORDS = {
    "early_years":          ["early years", "nursery", "jr. kg", "sr. kg"],
    "foundational_primary": ["foundational primary", "grade 1", "grades 1"],
    "preparatory":          ["preparatory", "grade 3", "grades 3"],
    "middle":               ["middle stage", "grade 6", "grades 6"],
}


def _pdf_to_b64_images(pdf_path: str, dpi: int = 200) -> list[str]:
    """Convert PDF pages to base64-encoded PNG strings."""
    images = convert_from_path(pdf_path, dpi=dpi)
    b64_list = []
    for img in images:
        # resize to max 1600px wide to keep memory reasonable on M4 16GB
        w, h = img.size
        if w > 1600:
            img = img.resize((1600, int(h * 1600 / w)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG")
        b64_list.append(base64.b64encode(buf.getvalue()).decode())
    return b64_list


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


def _call_ollama(images_b64: list[str], stage_hint: Optional[str],
                 model: str = DEFAULT_MODEL) -> str:
    """
    Send all page images in one prompt to Ollama.
    Ollama multimodal: images array in the request body.
    """
    hint = f"\n\nHINT: stage='{stage_hint}'" if stage_hint else ""
    prompt = PROMPT + hint + "\n\nExtract data from ALL pages shown. Return single JSON object."

    payload = {
        "model": model,
        "prompt": prompt,
        "images": images_b64,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": 8192,
        },
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()["response"]


class OllamaLPCExtractor:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._check_ollama()

    def _check_ollama(self):
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
            if not any(self.model.split(":")[0] in m for m in models):
                print(f"WARNING: Model '{self.model}' not found in Ollama.")
                print(f"Run:  ollama pull {self.model}")
        except requests.ConnectionError:
            raise RuntimeError(
                "Ollama not running. Start with: ollama serve\n"
                "Install:  brew install ollama\n"
                "Model:    ollama pull qwen2.5vl"
            )

    def extract_pdf(self, pdf_path: str) -> LPCRecord:
        pdf_path = str(pdf_path)
        stage_hint = _detect_stage(pdf_path)

        print(f"  Converting PDF to images ...")
        images_b64 = _pdf_to_b64_images(pdf_path, dpi=200)
        print(f"  {len(images_b64)} pages → sending to Ollama ({self.model}) ...")

        raw = _call_ollama(images_b64, stage_hint, self.model)

        # strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]

        data = json.loads(raw)

        # fill missing domains
        if "stage" in data and "months" in data:
            stage = data["stage"]
            expected = DOMAINS_BY_STAGE.get(stage, [])
            for m in data["months"]:
                for domain in expected:
                    m.setdefault("domains", {}).setdefault(domain, {
                        "c1": None, "c2": None, "c3": None, "c4": None,
                        "observational_anecdote": None,
                        "strengths": None, "focus_next_month": None,
                    })

        record = LPCRecord(**data)
        record.source_pdf = pdf_path
        return record
