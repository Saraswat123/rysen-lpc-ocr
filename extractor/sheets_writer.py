"""
Google Sheets push — one sheet per branch, 4 tabs per sheet.
Config: sheets_config.json (branch → sheet_id mapping).
Auth: GOOGLE_SERVICE_ACCOUNT_JSON in .env
"""
import json
import os
import pathlib
from typing import Optional

import gspread

from models.lpc import LPCRecord

CONFIG_PATH = pathlib.Path(__file__).parent.parent / "sheets_config.json"
TOKEN_PATH  = pathlib.Path(__file__).parent.parent / "oauth_token.json"

SHEET_TABS = {
    "early_years":           "Early Years (Nursery-KG)",
    "foundational_primary":  "Foundational Primary (Gr 1-2)",
    "preparatory":           "Preparatory Stage (Gr 3-5)",
    "middle":                "Middle Stage (Gr 6-8)",
}


def _get_client() -> gspread.Client:
    oauth_path = os.environ.get("GOOGLE_OAUTH_CLIENT")
    if not oauth_path or not pathlib.Path(oauth_path).exists():
        raise ValueError(
            "GOOGLE_OAUTH_CLIENT not set in .env\n"
            "Run: python setup_sheets.py  to authenticate."
        )
    return gspread.oauth(
        credentials_filename=oauth_path,
        authorized_user_filename=str(TOKEN_PATH),
    )


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise ValueError(
            "sheets_config.json not found.\n"
            "Run: python setup_sheets.py  to create all branch sheets."
        )
    return json.loads(CONFIG_PATH.read_text())


def push_records(records: list[LPCRecord]) -> dict[str, int]:
    """
    Push records to their branch-specific Google Sheet.
    Returns {branch: rows_pushed}.
    """
    gc = _get_client()
    config = _load_config()
    sig = lambda b: "✓" if b else ("✗" if b is False else "")

    # group rows by (branch, stage)
    groups: dict[tuple, list] = {}
    for rec in records:
        notes = rec.extraction_notes or ""
        branch = notes.split("[branch:")[1].split("]")[0] if "[branch:" in notes else "Unknown"

        key = (branch, rec.stage)
        if key not in groups:
            groups[key] = []

        for month_entry in rec.months:
            for domain, d in month_entry.domains.items():
                groups[key].append([
                    branch,
                    rec.student_name or "",
                    rec.class_sec or "",
                    rec.roll_no or "",
                    month_entry.month,
                    domain,
                    d.c1 if d.c1 is not None else "",
                    d.c2 if d.c2 is not None else "",
                    d.c3 if d.c3 is not None else "",
                    d.c4 if d.c4 is not None else "",
                    d.observational_anecdote or "",
                    d.strengths or "",
                    d.focus_next_month or "",
                    sig(rec.parent_sign_present),
                    sig(rec.teacher_sign_present),
                    sig(rec.principal_sign_present),
                    pathlib.Path(rec.source_pdf or "").name,
                ])

    results: dict[str, int] = {}
    for (branch, stage), rows in groups.items():
        if not rows:
            continue

        sheet_id = config.get(branch)
        if not sheet_id:
            print(f"[Sheets] No sheet configured for branch: {branch}")
            continue

        wb = gc.open_by_key(sheet_id)
        tab_name = SHEET_TABS.get(stage)
        if not tab_name:
            continue

        ws = wb.worksheet(tab_name)
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        results[branch] = results.get(branch, 0) + len(rows)
        print(f"[Sheets] {branch} → {tab_name}: {len(rows)} rows pushed")

    return results
