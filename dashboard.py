"""
RYSEN LPC Dashboard v2 — Upload only.
Talks to FastAPI backend. No data displayed here.
Run: streamlit run dashboard.py
"""
import json
import os
import pathlib
import time

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_URL = os.environ.get("API_URL", "http://localhost:8000")

_CONFIG_PATH = pathlib.Path(__file__).parent / "sheets_config.json"
SHEETS_CONFIG: dict = json.loads(_CONFIG_PATH.read_text()) if _CONFIG_PATH.exists() else {}

TEAL = "#1D4E6B"
GOLD = "#F5A623"

st.set_page_config(
    page_title="RYSEN LPC Upload",
    page_icon="📋",
    layout="centered",
)

st.markdown(f"""
<style>
  .header {{
    background:{TEAL}; color:white; padding:1.2rem 2rem;
    border-radius:8px; margin-bottom:1.5rem;
  }}
  .header h1 {{ margin:0; font-size:1.6rem; }}
  .header p  {{ margin:.2rem 0 0; opacity:.85; font-size:.9rem; }}
  .status-done    {{ color:#1a7a3c; font-weight:600; }}
  .status-fail    {{ color:#c0392b; font-weight:600; }}
  .status-pending {{ color:#888; }}
</style>
<div class="header">
  <h1>📋 RYSEN LPC Upload Portal</h1>
  <p>Select branch → upload scanned LPC PDFs → AI extracts automatically</p>
</div>
""", unsafe_allow_html=True)

BRANCHES = [
    "Beawar", "Beawar NLC 1", "Beawar NLC 2",
    "Bikaner - Virat Nagar", "Bikaner - Vyas Colony",
    "Nimbahera - Main", "Nimbahera - Preschool",
    "Deoli - Main", "Deoli - Preschool",
    "Jaisalmer",
    "Sri Ganganagar - Main", "Sri Ganganagar - Preschool",
    "Sri Vijaynagar", "Pilibanga",
    "Udaipur",
]

# ── Step 1: Location ─────────────────────────────────────────────────────────
st.markdown("### Step 1 — Select Location")
branch = st.selectbox("Branch / Campus", BRANCHES, label_visibility="collapsed")
st.markdown(f"<p style='color:{TEAL};font-weight:600;margin:0'>📍 {branch}</p>", unsafe_allow_html=True)

# ── Step 2: Student Details ───────────────────────────────────────────────────
st.markdown("---")
st.markdown("### Step 2 — Student Details")
c1, c2, c3, c4 = st.columns(4)
with c1:
    student_name = st.text_input("Student Name", placeholder="e.g. Rahul Sharma")
with c2:
    class_sec = st.text_input("Class", placeholder="e.g. 5A")
with c3:
    roll_no = st.text_input("Roll No.", placeholder="e.g. 12")
with c4:
    section = st.text_input("Section", placeholder="e.g. A")

if not student_name or not class_sec:
    st.warning("Fill Student Name and Class to continue.")
    st.stop()

# ── Step 3: Upload ────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### Step 3 — Upload LPC PDF")
files = st.file_uploader(
    "Drop scanned LPC PDF for this student",
    type=["pdf"],
    accept_multiple_files=False,
)

if files:
    st.info(f"Ready: `{files.name}` | Student: **{student_name}** | Class: **{class_sec}** | Roll: **{roll_no}**")

# ── Step 4: Submit ────────────────────────────────────────────────────────────
st.markdown("---")
if st.button("🚀 Upload & Extract", type="primary", use_container_width=True, disabled=not files):
    with st.spinner("Uploading to server..."):
        try:
            resp = requests.post(
                f"{API_URL}/api/upload",
                data={
                    "branch": branch,
                    "student_name": student_name,
                    "class_sec": f"{class_sec}{section}".strip(),
                    "roll_no": roll_no,
                },
                files=[("files", (files.name, files.read(), "application/pdf"))],
                timeout=60,
            )
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            st.error(f"Upload failed: {e}")
            st.stop()

    jobs = result.get("jobs", [])
    st.success(f"✓ PDF queued for extraction — {student_name} | {class_sec}{section} | Roll {roll_no}")

    # ── Track job status ──────────────────────────────────────────────────────
    st.markdown("### Extraction Progress")
    placeholders = {j["job_id"]: st.empty() for j in jobs}

    for job in jobs:
        placeholders[job["job_id"]].markdown(
            f"⏳ `{job['filename']}` — queued"
        )

    done_count = 0
    max_wait = 600  # 10 min timeout
    start = time.time()

    while done_count < len(jobs) and (time.time() - start) < max_wait:
        time.sleep(3)
        done_count = 0
        for job in jobs:
            try:
                s = requests.get(f"{API_URL}/api/jobs/{job['job_id']}", timeout=10).json()
                status = s.get("status", "pending")
                fname = job["filename"]

                if status == "done":
                    placeholders[job["job_id"]].markdown(
                        f'<span class="status-done">✓ `{fname}` — done</span>',
                        unsafe_allow_html=True,
                    )
                    done_count += 1
                elif status == "failed":
                    placeholders[job["job_id"]].markdown(
                        f'<span class="status-fail">✗ `{fname}` — failed: {s.get("error","")[:80]}</span>',
                        unsafe_allow_html=True,
                    )
                    done_count += 1
                elif status == "processing":
                    placeholders[job["job_id"]].markdown(f"🔄 `{fname}` — extracting...")
                else:
                    placeholders[job["job_id"]].markdown(f"⏳ `{fname}` — queued...")
            except Exception:
                pass

    if done_count == len(jobs):
        st.balloons()
        st.success("All PDFs processed! Data pushed to Google Sheets automatically.")
        sheet_id = SHEETS_CONFIG.get(branch)
        if sheet_id:
            sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
            st.markdown(f"[📊 Open {branch} Sheet]({sheet_url})")
    else:
        st.warning("Some jobs still running. Check Google Sheets in a few minutes.")

# ── Recent jobs (this branch) ─────────────────────────────────────────────────
st.markdown("---")
sheet_id = SHEETS_CONFIG.get(branch)
if sheet_id:
    st.markdown(
        f"[📊 View {branch} Google Sheet](https://docs.google.com/spreadsheets/d/{sheet_id}/edit)",
        unsafe_allow_html=False,
    )

st.markdown("### Recent Uploads")
try:
    jobs_resp = requests.get(f"{API_URL}/api/jobs", params={"branch": branch, "limit": 10}, timeout=5)
    if jobs_resp.ok:
        recent = jobs_resp.json()
        if recent:
            for j in recent:
                icon = {"done": "✅", "failed": "❌", "processing": "🔄", "pending": "⏳"}.get(j["status"], "❓")
                st.markdown(f"{icon} `{j['filename']}` — {j['status']} — {j['created_at'][:19]}")
        else:
            st.caption("No uploads yet for this branch.")
except Exception:
    st.caption("API not reachable — start the backend server.")
