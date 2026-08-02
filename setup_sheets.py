"""
One-time setup: creates one Google Sheet per branch, each with 4 tabs.
Saves branch → sheet_id mapping to sheets_config.json.

Run: python setup_sheets.py
Needs: GOOGLE_SERVICE_ACCOUNT_JSON in .env
"""
import json
import os
import pathlib
import time

import gspread
from dotenv import load_dotenv

load_dotenv()

BRANCHES = [
    "Beawar",
    "Beawar NLC 1",
    "Beawar NLC 2",
    "Bikaner - Virat Nagar",
    "Bikaner - Vyas Colony",
    "Nimbahera - Main",
    "Nimbahera - Preschool",
    "Deoli - Main",
    "Deoli - Preschool",
    "Jaisalmer",
    "Sri Ganganagar - Main",
    "Sri Ganganagar - Preschool",
    "Sri Vijaynagar",
    "Pilibanga",
    "Udaipur",
]

SHEET_TABS = [
    "Early Years (Nursery-KG)",
    "Foundational Primary (Gr 1-2)",
    "Preparatory Stage (Gr 3-5)",
    "Middle Stage (Gr 6-8)",
]

HEADERS = [
    "Branch", "Student Name", "Class & Sec", "Roll No.", "Month", "Domain",
    "C1", "C2", "C3", "C4",
    "Observational Anecdote", "Strengths", "Focus for Next Month",
    "Parent Sign", "Teacher Sign", "Principal Sign", "Source PDF"
]

SHARE_WITH = "saraswat.das@aits.group"  # gets editor access on all sheets


def get_client():
    oauth_path = os.environ.get("GOOGLE_OAUTH_CLIENT")
    token_path = pathlib.Path(__file__).parent / "oauth_token.json"
    if not oauth_path or not pathlib.Path(oauth_path).exists():
        raise ValueError("GOOGLE_OAUTH_CLIENT not set in .env")
    return gspread.oauth(
        credentials_filename=oauth_path,
        authorized_user_filename=str(token_path),
    )


def setup_branch_sheet(gc, branch: str) -> str:
    """Create one sheet for a branch with 4 tabs + headers. Returns sheet ID."""
    title = f"RYSEN LPC — {branch}"
    print(f"  Creating: {title} ...", end=" ")

    wb = gc.create(title)

    # rename default Sheet1 to first tab
    default = wb.sheet1
    default.update_title(SHEET_TABS[0])
    default.append_row(HEADERS)
    default.freeze(rows=1)

    # create remaining 3 tabs
    for tab_name in SHEET_TABS[1:]:
        ws = wb.add_worksheet(title=tab_name, rows=1000, cols=len(HEADERS))
        ws.append_row(HEADERS)
        ws.freeze(rows=1)
        time.sleep(0.5)  # avoid rate limit

    # share with org account
    wb.share(SHARE_WITH, perm_type="user", role="writer", notify=False)

    sheet_id = wb.id
    print(f"✓ {sheet_id}")
    return sheet_id


def main():
    print("RYSEN LPC — Google Sheets Setup")
    print("=" * 50)

    config_path = pathlib.Path("sheets_config.json")

    # load existing config to skip already-created sheets
    existing = {}
    if config_path.exists():
        existing = json.loads(config_path.read_text())
        print(f"Found {len(existing)} existing sheets in config.")

    gc = get_client()
    config = dict(existing)

    for branch in BRANCHES:
        if branch in config:
            print(f"  Skip (exists): {branch}")
            continue
        sheet_id = setup_branch_sheet(gc, branch)
        config[branch] = sheet_id
        # save after each creation (safe if interrupted)
        config_path.write_text(json.dumps(config, indent=2))
        time.sleep(1)

    print("\n" + "=" * 50)
    print(f"Done. {len(config)} sheets configured.")
    print(f"Config saved: {config_path}")
    print("\nSheet links:")
    for branch, sid in config.items():
        print(f"  {branch}: https://docs.google.com/spreadsheets/d/{sid}/edit")


if __name__ == "__main__":
    main()
