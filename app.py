# app.py — Databricks Apps ready, no secrets needed

import os
import io
import re
import json
import math
import sqlite3
import unicodedata
from datetime import datetime, date
from typing import List, Dict, Tuple, Optional

import streamlit as st
import pandas as pd
import PyPDF2

# Optional libs (safe fallbacks if not present)
try:
    import numpy as np  # noqa
except Exception:
    np = None

# ---- Plotly is optional; if not present we fall back to st.bar_chart ----
_HAS_PLOTLY = True
try:
    import plotly.express as px  # noqa
except Exception:
    _HAS_PLOTLY = False

# =========================
# PERSISTENT STORAGE PICKER
# =========================
def _is_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        p = os.path.join(path, ".write_test")
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(p)
        return True
    except Exception:
        return False

def pick_data_root(app_subdir="hr_people_ai"):
    """Choose a persistent folder:
       1) PERSIST_DIR (e.g., /Volumes/<cat>/<schema>/<vol>/myapp)
       2) /dbfs/FileStore/apps/<app_subdir> if /dbfs is mounted and writable
       3) local ./data (persists across restarts but not 'reset environment')"""
    persist_dir = os.getenv("PERSIST_DIR", "").strip()

    if persist_dir and _is_writable(persist_dir):
        return persist_dir, f"Using PERSIST_DIR at {persist_dir}"

    dbfs_candidate = f"/dbfs/FileStore/apps/{app_subdir}"
    if os.path.isdir("/dbfs") and _is_writable(dbfs_candidate):
        return dbfs_candidate, f"Using DBFS at {dbfs_candidate}"

    local_candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(local_candidate, exist_ok=True)
    return local_candidate, f"Using LOCAL folder at {local_candidate} (may reset on full redeploy)."

DATA_ROOT, STORAGE_NOTE = pick_data_root("hr_people_ai")
DB_FILE   = os.path.join(DATA_ROOT, "hr_ai.db")
JD_FOLDER = os.path.join(DATA_ROOT, "job_descriptions")
os.makedirs(JD_FOLDER, exist_ok=True)

# ====================================
# DATABRICKS MODEL SERVING LLM BRIDGE
# ====================================
# No secrets needed: uses the App's OAuth behind the scenes.
from tenacity import retry, stop_after_attempt, wait_exponential
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, BadRequest

# Keep Llama-4 Maverick as default
DEFAULT_ENDPOINT = os.getenv("ENDPOINT_NAME", "databricks-llama-4-maverick")

def _try_openai_client(w: WorkspaceClient):
    get_client = getattr(w.serving_endpoints, "get_open_ai_client", None)
    return get_client() if callable(get_client) else None

@retry(reraise=True, stop=stop_after_attempt(6), wait=wait_exponential(multiplier=1, min=1, max=20))
def dbx_chat(messages, model=None, temperature=0.2, max_tokens=1024) -> str:
    endpoint = model or DEFAULT_ENDPOINT
    w = WorkspaceClient()

    # Path 1: new SDK helper (preferred)
    client = _try_openai_client(w)
    if client:
        resp = client.chat.completions.create(
            model=endpoint, messages=messages,
            temperature=temperature, max_tokens=max_tokens
        )
        return resp.choices[0].message.content

    # OpenAI-shaped payload
    payload = {
        "model": endpoint,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # Path 2a: OpenAI REST shape on Serving
    try:
        r = w.api_client.do(
            "POST",
            f"/serving-endpoints/{endpoint}/openai/v1/chat/completions",
            body=payload,
        )
        return r["choices"][0]["message"]["content"]
    except (NotFound, BadRequest):
        pass

    # Path 2b: generic /invocations fallback (older handlers)
    for body in [
        payload,
        {"messages": messages, "temperature": temperature, "max_tokens": max_tokens},
        {"input": {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}},
        {"inputs": {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}},
    ]:
        try:
            r = w.api_client.do("POST", f"/serving-endpoints/{endpoint}/invocations", body=body)
            if isinstance(r, dict):
                if "choices" in r and r["choices"]:
                    return r["choices"][0]["message"]["content"]
                if "predictions" in r and r["predictions"]:
                    first = r["predictions"][0]
                    if isinstance(first, dict):
                        return first.get("content") or first.get("text") or str(first)
                    return str(first)
                if "output_text" in r:
                    return r["output_text"]
            return str(r)
        except Exception:
            continue
    raise RuntimeError(f"Failed to query endpoint '{endpoint}'.")

# ================
# DB INITIALISATION
# ================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS job_descriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position TEXT,
            job_title TEXT,
            grade TEXT,
            filepath TEXT,
            content TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS position_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS talent_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT
        )
    """)
    conn.commit()
    conn.close()

# =========================
# AUTH (simple page gate)
# =========================
def _login_gate(dashboard_name: str = "AI Powered HR People Modelling"):
    if "auth" not in st.session_state:
        st.session_state.auth = {"logged_in": False, "user": None}
    if st.session_state.auth["logged_in"]:
        with st.sidebar:
            st.markdown("✅ **Logged in**")
            st.markdown(f"**User:** `{st.session_state.auth['user']}`")
            if st.button("Log out", use_container_width=True):
                st.session_state.auth = {"logged_in": False, "user": None}
                st.rerun()
        return
    st.title(f"⚡ {dashboard_name}")
    st.caption("Sign in to continue")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if username == "admin" and password == "genco2025":
            st.session_state.auth = {"logged_in": True, "user": username}
            st.success("Welcome! Redirecting…")
            st.rerun()
        else:
            st.error("Invalid username or password.")
    st.stop()

# ============================================
# JD AUTO-MATCH + HELPERS (unchanged logic)
# ============================================
PROJECT_SYNONYMS = {
    "NHEP": "Projek NHEP",
    "HHFS": "Projek HHFS",
    "HESS": "Projek HESS",
    "NENGGIRI": "Projek NHEP",
}

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = re.sub(r"[_\-&]+", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

def normalize_project_from_filename(path_or_name: str) -> Optional[str]:
    root = os.path.splitext(os.path.basename(str(path_or_name)))[0]
    first_token = re.split(r"[_\s\-]+", root, maxsplit=1)[0].upper()
    return PROJECT_SYNONYMS.get(first_token)

def position_from_filename(path_or_name: str) -> str:
    root = os.path.splitext(os.path.basename(str(path_or_name)))[0]
    parts = re.split(r"[_]+", root)
    if not parts:
        return root
    if parts[0].upper() in PROJECT_SYNONYMS:
        parts = parts[1:]
    title = " ".join(parts).strip()
    title = re.sub(r"\b(Senior|Principal|Lead|Chief|Assistant|Associate)\s+Engineer\s+(.*)$",
                   r"\1 Engineer - \2", title, flags=re.I)
    title = re.sub(r"\s{2,}", " ", title)
    return title

def score_match(user_project: str, user_position: str, fname: str) -> float:
    jp = normalize_project_from_filename(fname) or ""
    pos = position_from_filename(fname)
    s_user_project = _slug(user_project)
    s_user_position = _slug(user_position)
    s_jp = _slug(jp)
    s_pos = _slug(pos)
    proj_score = 1.0 if s_user_project == s_jp else 0.0
    u_tokens = set(s_user_position.split())
    f_tokens = set(s_pos.split())
    overlap = len(u_tokens & f_tokens)
    pos_score = overlap / max(1, len(u_tokens))
    return proj_score * 0.6 + pos_score * 0.4

def automatch_jd(jd_files: List[str], user_project: str, user_position: str) -> Tuple[str, float]:
    if not jd_files:
        return "", 0.0
    scored = sorted(
        [(f, score_match(user_project, user_position, f)) for f in jd_files],
        key=lambda x: x[1], reverse=True
    )
    return scored[0]

def _pretty_name(path: str) -> str:
    if not path:
        return "(none)"
    return f"{os.path.basename(path)} → [{normalize_project_from_filename(path) or 'Unknown Project'}] / [{position_from_filename(path)}]"

def jd_selector_ui(jd_files: List[str], user_project: str, user_position: str, st_module):
    best_path, conf = automatch_jd(jd_files, user_project, user_position)
    st_module.write(f"**Auto-matched JD:** {_pretty_name(best_path)} (confidence {conf:.2f})")
    show_filter = (conf < 0.85) or st_module.checkbox("Filter/override JD manually", value=False)
    chosen = best_path
    if show_filter:
        filt_project = st_module.selectbox(
            "Filter JD by Project",
            options=["(All)"] + sorted(set([normalize_project_from_filename(f) or "(Unknown)" for f in jd_files])),
            index=0
        )
        filtered = jd_files
        if filt_project != "(All)":
            filtered = [f for f in filtered if (normalize_project_from_filename(f) or "(Unknown)") == filt_project]
        q = st_module.text_input("Filter JD by Position Title (contains)", value=user_position)
        if q.strip():
            s_q = _slug(q)
            filtered = [f for f in filtered if s_q in _slug(position_from_filename(f))]
        opts = [f"{_pretty_name(f)}" for f in filtered] or ["(No files)"]
        sel = st_module.selectbox("Choose JD file (override)", options=opts, index=0)
        if filtered:
            chosen = filtered[opts.index(sel)]
    return chosen

# ==============================
# EXISTING HELPERS (as provided)
# ==============================
def extract_field(content, label):
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if label.lower() in line.lower():
            if i+1 < len(lines) and not any(lbl in lines[i+1] for lbl in ["Title", "Grade"]):
                return lines[i+1].strip()
            return line.split(":", 1)[-1].strip()
    return None

def extract_competencies_from_jd(jd_content):
    match = re.search(
        r"(?:Competency\s*Requirement|Competencies?|Core\s*Competencies?)\s*[:]?\s*([\s\S]+?)(?=\n\s*(Education|Education\s+and\s+Knowledge|Certifications?|Experience|Personality|Networking|Job\s+Requirements?|$))",
        jd_content, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    return "Not specified"

def extract_education_from_jd(jd_content):
    match = re.search(
        r"(Education(?:\s+and\s+Knowledge)?[\s\S]*?)(?=\n\s*(Experience|Skills|Competencies|Certifications|Personality|Networking|Job\s+Requirements?|$))",
        jd_content, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    return "Not specified"

ENGINEERING_FAMILY = {
    "engineering", "engineer", "engg",
    "civil", "electrical", "mechanical", "mechatronic", "mechatronics",
    "electronic", "electronics", "instrument", "instrumentation", "control",
    "c&i", "automation", "chemical", "petroleum", "aerospace",
    "industrial", "manufacturing", "materials", "marine", "naval", "power",
    "telecommunication", "telecommunications", "computer engineering"
}
CIVIL_KEYS = {"civil", "structural"}
ELECTRICAL_KEYS = {"electrical", "power", "telecommunication", "telecommunications"}
MECHANICAL_KEYS = {"mechanical", "mechatronic", "mechatronics", "industrial", "manufacturing"}
ELECTRONIC_KEYS = {"electronic", "electronics", "instrument", "instrumentation", "control", "c&i", "automation"}

LAW_FAMILY = {"law", "legal", "llb", "juris", "jurisprudence", "syariah", "sharia"}
FINANCE_FAMILY = {"finance", "financial", "accounting", "accountancy", "accountant", "acca", "cpa", "cfa", "economics", "banking"}

def clean_text(x: str) -> str:
    return _slug(x or "")

def jd_required_disciplines(jd_text: str) -> Dict[str, bool]:
    t = clean_text(jd_text)
    allow_law = any(k in t for k in LAW_FAMILY)
    allow_finance = any(k in t for k in FINANCE_FAMILY)
    civil_only = bool(re.search(r"\b(civil)\s+engineering\b", t))
    electrical_only = bool(re.search(r"\b(electrical|power)\s+engineering\b", t))
    mechanical_only = bool(re.search(r"\b(mechanical|mechatronic[s]?)\s+engineering\b", t))
    electronic_only = bool(re.search(r"\b(electronic[s]?|instrument(ation)?|control)\s+engineering\b", t))
    any_engineering = "engineering" in t or "engineer" in t
    narrow_selected = civil_only or electrical_only or mechanical_only or electronic_only
    return {
        "any_engineering": any_engineering and not narrow_selected,
        "civil_only": civil_only,
        "electrical_only": electrical_only,
        "mechanical_only": mechanical_only,
        "electronic_only": electronic_only,
        "allow_law": allow_law,
        "allow_finance": allow_finance,
    }

def education_to_buckets(education_text: str) -> Dict[str, bool]:
    t = clean_text(education_text)
    has_any_engineering = any(k in t for k in ENGINEERING_FAMILY)
    has_civil = any(k in t for k in CIVIL_KEYS)
    has_electrical = any(k in t for k in ELECTRICAL_KEYS)
    has_mechanical = any(k in t for k in MECHANICAL_KEYS)
    has_electronic = any(k in t for k in ELECTRONIC_KEYS)
    has_law = any(k in t for k in LAW_FAMILY)
    has_finance = any(k in t for k in FINANCE_FAMILY)
    return {
        "any_engineering": has_any_engineering,
        "civil": has_civil,
        "electrical": has_electrical,
        "mechanical": has_mechanical,
        "electronic": has_electronic,
        "law": has_law,
        "finance": has_finance,
    }

def matches_requirement(candidate_buckets: Dict[str,bool], rule: Dict[str,bool]) -> bool:
    if rule["allow_law"] and candidate_buckets["law"]:
        return True
    if rule["allow_finance"] and candidate_buckets["finance"]:
        return True
    if rule["any_engineering"] and candidate_buckets["any_engineering"]:
        return True
    if rule["civil_only"] and candidate_buckets["civil"]:
        return True
    if rule["electrical_only"] and candidate_buckets["electrical"]:
        return True
    if rule["mechanical_only"] and candidate_buckets["mechanical"]:
        return True
    if rule["electronic_only"] and candidate_buckets["electronic"]:
        return True
    return False

def shortlist_by_education_people_model(df_candidates: pd.DataFrame, people_edu_col: str, jd_edu_text: str):
    if people_edu_col not in df_candidates.columns:
        return df_candidates.iloc[0:0].copy(), {"error": f"Column '{people_edu_col}' not found"}
    rule = jd_required_disciplines(jd_edu_text)
    mask = []
    for _, row in df_candidates.iterrows():
        buckets = education_to_buckets(str(row.get(people_edu_col, "")))
        mask.append(matches_requirement(buckets, rule))
    return df_candidates.loc[mask].copy(), rule

def education_matches(jd_text, candidate_edu):
    jd_text = jd_text.lower()
    candidate_edu = str(candidate_edu).lower()
    if not candidate_edu or candidate_edu == "not provided":
        return False
    if "electrical" in jd_text:
        return any(x in candidate_edu for x in ["electrical", "instrumentation", "control"])
    if "mechanical" in jd_text:
        return "mechanical" in candidate_edu
    if "civil" in jd_text:
        return "civil" in candidate_edu
    if "instrumentation" in jd_text or "control" in jd_text:
        return any(x in candidate_edu for x in ["instrumentation", "control", "electrical"])
    if any(x in jd_text for x in ["any engineering", "engineering or related", "or related fields", "equivalent"]):
        return "engineering" in candidate_edu
    return "engineering" in candidate_edu

def grade_matches(candidate_grade: str, selected_grade: str) -> bool:
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", str(s or "").upper())
    def extract_grade_tokens(s: str):
        s = norm(s)
        toks = re.findall(r'(?:CM|GM|M|E)\d{2}', s)
        return toks or ([s] if s else [])
    sel = norm(selected_grade)
    mapping = {
        "M16": {"M16", "CM16", "GM16", "M15", "CM15", "GM15"},
        "M15": {"M15", "CM15", "GM15", "E17", "E16"},
        "E16": {"E17", "E16", "E15", "E14"},
        "E17": {"E17", "E16", "E15", "E14"},
        "E16&E17": {"E17", "E16", "E15", "E14"},
        "E14": {"E15", "E14", "E13", "E12"},
        "E15": {"E15", "E14", "E13", "E12"},
        "E14&E15": {"E15", "E14", "E13", "E12"},
        "E12": {"E12", "E13"},
        "E13": {"E12", "E13"},
        "E12&E13": {"E12", "E13"},
    }
    if sel not in mapping:
        compact = sel.replace(" ", "")
        if compact in {"E16E17", "E14E15", "E12E13"}:
            sel = compact[:3] + "&" + compact[3:]
    if sel not in mapping:
        jd_range = re.findall(r"E\d+", sel)
        if not jd_range:
            return False
        if len(jd_range) == 1:
            wanted = jd_range[0]
            cand_tokens = extract_grade_tokens(candidate_grade)
            return any(tok == wanted for tok in cand_tokens)
        else:
            try:
                start = int(jd_range[0][1:])
                end = int(jd_range[1][1:])
                cand_tokens = extract_grade_tokens(candidate_grade)
                for tok in cand_tokens:
                    m = re.match(r"E(\d{2})$", tok)
                    if m:
                        val = int(m.group(1))
                        if start <= val <= end:
                            return True
                return False
            except Exception:
                return False
    allowed = mapping[sel]
    cand_tokens = extract_grade_tokens(candidate_grade)
    return any(tok in allowed for tok in cand_tokens)

def get_mapped_titles(position):
    mapping = {
        "Project Manager": ["Project Manager", "Principal Engineer", "Senior Manager"],
        "Principal Engineer": ["Principal Engineer", "Senior Engineer", "Manager", "Senior Manager"],
        "Senior Engineer": ["Senior Engineer", "Engineer", "Manager", "Executive"],
        "Engineer": ["Engineer", "Executive"]
    }
    for level, titles in mapping.items():
        if level.lower() in position.lower():
            return titles
    return []

def strict_job_title_match(candidate_title, mapped_titles):
    candidate_title = candidate_title.lower().strip()
    for mt in mapped_titles:
        mt_norm = mt.lower().strip()
        if candidate_title.startswith(mt_norm):
            return True
    return False

def convert_for_json(val):
    if isinstance(val, (pd.Timestamp, datetime, date)):
        return str(val)
    return val

def format_date(value):
    if pd.isna(value) or str(value).strip() in ["", "Not provided"]:
        return "Not provided"
    value = str(value).strip().split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %B %Y", "%b %Y", "%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%d-%m-%Y")
        except Exception:
            continue
    try:
        return pd.to_datetime(value, errors="coerce").strftime("%d-%m-%Y")
    except Exception:
        return value

# =========================
# TALENT CARD PARSER
# =========================
def extract_talent_card_fields(pdf_file):
    reader = PyPDF2.PdfReader(pdf_file)
    text = "\n".join([p.extract_text() for p in reader.pages if p.extract_text()]) or ""
    st.subheader("Raw Talent Card Text Preview (first 1000 chars):")
    st.code(text[:1000])
    text = re.sub(r"[ \t]+", " ", text)
    ordered_labels = [
        "Strength",
        "Significant Achievements / Contributions",
        "Professional Certifications",
        "Education",
        "Work Experience",
        "Skills & Expertise",
        "Leadership Experience",
        "Honours & Awards",
        "Career Goals",
        "Job Preference",
        "Project Experience",
    ]
    def extract_section(label: str, src: str) -> str:
        idx = ordered_labels.index(label)
        rest = ordered_labels[idx + 1 :]
        next_alt = "|".join([re.escape(l) for l in rest]) if rest else "$"
        pattern = rf"{re.escape(label)}\s*[:\-]?\s*(.*?)(?=\n(?:{next_alt})\s*[:\-]?\s*|$)"
        m = re.search(pattern, src, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return "Not provided"
        val = m.group(1).strip()
        val = re.sub(r"\s*\n\s*", " ", val)
        val = re.sub(r"^[•\-\u2022]+\s*", "", val)
        return val if val else "Not provided"

    def grab(regex, fallback="Not provided"):
        m = re.search(regex, text, re.IGNORECASE)
        return m.group(1).strip() if m else fallback

    result = {}
    result["Name"] = grab(r"NAME\s*:?\s*(.+)")
    result["Grade"] = grab(r"Grade\s*:?\s*([A-Z0-9]+)")
    result["Age"]   = grab(r"Age\s*:?\s*(\d+)")
    result["Date Joined"] = grab(r"Date\s*Joined\s*TNB\s*:?\s*([^\n]+)")
    result["Permanent Date"] = grab(r"Permanent\s*:?\s*([^\n]+)")
    result["Retirement Date"] = grab(r"Retirement\s*Date\s*:?\s*([^\n]+)")
    result["Strengths"] = extract_section("Strength", text)
    result["Achievements"] = extract_section("Significant Achievements / Contributions", text)
    result["Professional Certifications"] = extract_section("Professional Certifications", text)
    result["Education"] = extract_section("Education", text)
    result["Work Experience"] = extract_section("Work Experience", text)
    result["Skills/Expertise"] = extract_section("Skills & Expertise", text)
    result["Leadership"] = extract_section("Leadership Experience", text)
    result["Awards"] = extract_section("Honours & Awards", text)
    result["Career Goals"] = extract_section("Career Goals", text)
    result["Job Preferences"] = extract_section("Job Preference", text)
    result["Project Experience"] = extract_section("Project Experience", text)

    try:
        fname = getattr(pdf_file, 'name', None)
        if fname is None and hasattr(pdf_file, '_file'):
            fname = getattr(pdf_file._file, 'name', None)
        if fname:
            match = re.search(r"(\d{8,})", fname)
            result["Employee ID"] = match.group(1) if match else "Not provided"
        else:
            result["Employee ID"] = "Not provided"
    except Exception:
        result["Employee ID"] = "Not provided"
    return result

def refresh_talent_cards_session():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT id, data FROM talent_cards").fetchall()
    conn.close()
    if rows:
        df = pd.DataFrame([{**json.loads(d), "DB_ID": i} for i, d in rows])
    else:
        df = pd.DataFrame()
    st.session_state['talent_cards'] = df

# =========================
# UPLOAD PAGES
# =========================
def upload_talent_cards():
    st.header("🧑‍🎓 Upload Talent Card(s) (PDF)")
    files = st.file_uploader("Upload one or more Talent Card PDFs", type=["pdf"], accept_multiple_files=True)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if files:
        stored = 0
        for f in files:
            try:
                card = extract_talent_card_fields(f)
                c.execute("INSERT INTO talent_cards (data) VALUES (?)", (json.dumps(card),))
                stored += 1
                st.success(f"Stored Talent Card: {card.get('Name','Unknown')}")
            except Exception as e:
                st.error(f"Failed to extract {getattr(f,'name','file')}: {e}")
        if stored:
            conn.commit()
    else:
        st.info("No Talent Card PDFs uploaded yet.")
    rows = c.execute("SELECT id, data FROM talent_cards").fetchall()
    conn.close()
    st.subheader("📋 Loaded Talent Cards")
    if rows:
        df_raw = pd.DataFrame([{**json.loads(d), "DB_ID": i} for i, d in rows])
        def to_employee_data(row):
            keys_to_keep = {"Name","Grade","Age","DB_ID"}
            blob = []
            for k, v in row.items():
                if k not in keys_to_keep:
                    if pd.notna(v) and str(v).strip() != "":
                        blob.append(f"{k}: {v}")
            return "; ".join(blob) if blob else "Not provided"
        disp = pd.DataFrame({
            "Name": df_raw.get("Name", "Not provided"),
            "Grade": df_raw.get("Grade", "Not provided"),
            "Age": df_raw.get("Age", "Not provided"),
            "Employee Data": df_raw.apply(to_employee_data, axis=1)
        })
        st.dataframe(disp)
        del_id = st.number_input("Enter Talent Card DB_ID to delete row:", min_value=0, step=1, key="del_talent_card")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("❌ Delete Talent Card Row"):
                conn = sqlite3.connect(DB_FILE)
                conn.execute("DELETE FROM talent_cards WHERE id=?", (del_id,))
                conn.commit()
                conn.close()
                st.success(f"Deleted Talent Card record with ID {del_id}.")
        with col2:
            if st.button("🗑️ Delete All Talent Cards"):
                conn = sqlite3.connect(DB_FILE)
                conn.execute("DELETE FROM talent_cards")
                conn.commit()
                conn.close()
                st.warning("⚠️ All talent cards deleted.")
    else:
        st.info("No Talent Cards stored yet.")
    refresh_talent_cards_session()

def upload_position_profiles():
    st.header("📊 Upload Position Profiles (Excel)")
    file = st.file_uploader("Upload Position Profiles Excel", type=["xlsx"])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS position_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT
        )
    """)
    if file:
        df = pd.read_excel(file)
        df = df.where(pd.notnull(df), None)
        for _, row in df.iterrows():
            row_dict = {k: convert_for_json(v) for k, v in row.to_dict().items()}
            c.execute("INSERT INTO position_profiles (data) VALUES (?)", (json.dumps(row_dict),))
        conn.commit()
        st.success("✅ Position profiles uploaded and saved to database!")
    rows = c.execute("SELECT id, data FROM position_profiles").fetchall()
    if rows:
        st.subheader("📋 Uploaded Position Profiles")
        display_df = pd.DataFrame([{**json.loads(row[1]), "DB_ID": row[0]} for row in rows])
        st.dataframe(display_df)
        del_id = st.number_input("Enter Position Profile DB_ID to delete row:", min_value=0, step=1, key="del_position_profile")
        if st.button("❌ Delete Position Profile Row"):
            c.execute("DELETE FROM position_profiles WHERE id=?", (del_id,))
            conn.commit()
            st.success(f"Deleted position profile record with ID {del_id}.")
        if st.button("🗑️ Delete All Position Profiles"):
            c.execute("DELETE FROM position_profiles")
            conn.commit()
            st.warning("⚠️ All position profile records deleted.")
    conn.close()
    rows = []
    conn = sqlite3.connect(DB_FILE)
    for id_, data in conn.execute("SELECT id, data FROM position_profiles").fetchall():
        row_dict = json.loads(data)
        row_dict["DB_ID"] = id_
        rows.append(row_dict)
    conn.close()
    if rows:
        st.session_state['position_profiles'] = pd.DataFrame(rows)

def upload_job_descriptions():
    st.header("📄 Upload Job Descriptions (PDF)")
    files = st.file_uploader("Upload multiple PDF Job Descriptions", type=["pdf"], accept_multiple_files=True)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if files:
        for file in files:
            position = os.path.splitext(file.name)[0].replace("_", " ").replace(" JD", "").strip()
            reader = PyPDF2.PdfReader(file)
            content = " ".join([p.extract_text() for p in reader.pages if p.extract_text()])
            job_title = extract_field(content, "Title") or position_from_filename(file.name)
            grade = extract_field(content, "Grade")
            save_path = os.path.join(JD_FOLDER, f"{os.path.splitext(file.name)[0]}.pdf")
            with open(save_path, "wb") as f_out:
                f_out.write(file.getbuffer())
            c.execute(
                "INSERT INTO job_descriptions (position, job_title, grade, filepath, content) VALUES (?, ?, ?, ?, ?)",
                (position, job_title, grade, save_path, content)
            )
            st.success(f"✅ Stored JD: {job_title} | Grade: {grade}")
        conn.commit()
    st.subheader("📋 Uploaded Job Descriptions")
    jd_df = pd.read_sql_query("SELECT id, position, job_title, grade, filepath FROM job_descriptions", conn)
    st.dataframe(jd_df)
    delete_row = st.number_input("Enter JD ID to delete row:", min_value=0, step=1, key="del_jd")
    if st.button("❌ Delete JD Row"):
        c.execute("DELETE FROM job_descriptions WHERE id=?", (delete_row,))
        conn.commit()
        st.success(f"Deleted JD record with ID {delete_row}.")
    if st.button("🗑️ Delete All Job Descriptions"):
        conn.execute("DELETE FROM job_descriptions")
        conn.commit()
        st.warning("⚠️ All job descriptions deleted.")
    conn.close()

def upload_people_model():
    st.header("👥 Upload People Model Data (Excel)")
    file = st.file_uploader("Upload Excel People Model", type=["xlsx"])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if file:
        df = pd.read_excel(file)
        df["Name"] = df["Name"].astype(str).str.strip()
        df["Job Title"] = df["Job Title"].astype(str).str.strip()
        df["Employee ID"] = df["Employee ID"].astype(str).str.strip()
        df = df[(df["Name"] != "") & (df["Job Title"] != "") & df["Name"].notna() & df["Job Title"].notna() & df["Employee ID"].notna() & (df["Employee ID"] != "")]
        df = df.drop_duplicates(subset=["Employee ID"])
        def convert_for_json_people(val):
            if isinstance(val, (pd.Timestamp, datetime, date)):
                return str(val)
            return val
        for _, row in df.iterrows():
            row_dict = {k: convert_for_json_people(v) for k, v in row.to_dict().items()}
            c.execute("INSERT INTO candidates (data) VALUES (?)", (json.dumps(row_dict),))
        conn.commit()
        st.success("✅ People model data uploaded successfully!")
    rows = c.execute("SELECT id, data FROM candidates").fetchall()
    if rows:
        st.subheader("📋 Uploaded Candidates")
        display_df = pd.DataFrame([{**json.loads(row[1]), "DB_ID": row[0]} for row in rows])
        st.dataframe(display_df)
        del_id = st.number_input("Enter Candidate DB_ID to delete row:", min_value=0, step=1, key="del_candidate")
        if st.button("❌ Delete Candidate Row"):
            c.execute("DELETE FROM candidates WHERE id=?", (del_id,))
            conn.commit()
            st.success(f"Deleted candidate record with ID {del_id}.")
        if st.button("🗑️ Delete All Candidates"):
            c.execute("DELETE FROM candidates")
            conn.commit()
            st.warning("⚠️ All candidate records deleted.")
    conn.close()

# =========================
# SUCCESSION / AI PAGE (existing, unchanged)
# =========================
def succession_recommendation():
    st.header("🏆 AI Powered People Modelling")

    if 'position_profiles' not in st.session_state or st.session_state['position_profiles'].empty:
        st.warning("⚠️ Please upload Position Profiles first.")
        return

    refresh_talent_cards_session()
    talent_df = st.session_state.get('talent_cards', pd.DataFrame())
    profiles_df = st.session_state['position_profiles']

    # Allow runtime switch of Serving Endpoint (optional convenience)
    with st.sidebar.expander("Model endpoint"):
        default_ep = st.session_state.get("model_endpoint", DEFAULT_ENDPOINT)
        ep = st.text_input("Serving Endpoint name", value=default_ep, help="e.g., databricks-llama-4-maverick")
        st.session_state["model_endpoint"] = ep

    selected_project = st.selectbox("Select Project:", profiles_df['Projek'].unique())
    filtered_proj_df = profiles_df[profiles_df['Projek'] == selected_project].copy()
    filtered_proj_df['PositionID'] = filtered_proj_df['Position Title'] + " - " + filtered_proj_df['Job Text']
    selected_position_row = st.selectbox("Select Position Title:", filtered_proj_df['PositionID'].unique())
    sel_row = filtered_proj_df[filtered_proj_df['PositionID'] == selected_position_row].iloc[0]

    selected_job_title = sel_row['Position Title']
    selected_job_text = sel_row['Job Text']
    selected_grade = sel_row['Position Grade']
    project_name = sel_row['Projek']
    budget = sel_row.get('Budget', 'Not specified')

    # Choose JD
    conn = sqlite3.connect(DB_FILE)
    jd_df_db = pd.read_sql_query("SELECT position, job_title, grade, filepath, content FROM job_descriptions", conn)
    conn.close()

    jd_files = [p for p in jd_df_db['filepath'].dropna().tolist() if os.path.isfile(p)]
    if not jd_files:
        st.warning("⚠️ No JD files found. Please upload JDs first.")
        return

    chosen_jd_path = jd_selector_ui(jd_files, user_project=project_name, user_position=selected_job_title, st_module=st)
    chosen_row = jd_df_db[jd_df_db['filepath'] == chosen_jd_path]
    if chosen_row.empty:
        st.error("Chosen JD not found in database. Please re-upload.")
        return
    jd_content = chosen_row.iloc[0]['content'] or "Not found"
    jd_education = extract_education_from_jd(jd_content)
    selected_competencies = extract_competencies_from_jd(jd_content)

    edu_text = jd_education.replace("\n", "<br>").replace("-", "•")
    comp_text = selected_competencies.replace("\n", "<br>").replace("-", "•")
    st.markdown(
        f"""
        <div style="text-align: left;">
          <b>Project:</b> {project_name}<br>
          <b>Position:</b> {selected_job_title} ({selected_job_text})<br>
          <b>Grade:</b> {selected_grade}<br>
          <b>Budget:</b> {budget}<br><br>

          <b>Education Requirement:</b><br>
          {edu_text}<br><br>

          <b>Competency Requirement:</b><br>
          {comp_text}
        </div>
        """,
        unsafe_allow_html=True
    )

    # Load candidates
    conn = sqlite3.connect(DB_FILE)
    candidate_rows = [json.loads(row[0]) for row in conn.execute("SELECT data FROM candidates").fetchall()]
    conn.close()
    if not candidate_rows:
        st.warning("⚠️ No candidate data uploaded.")
        return
    df = pd.DataFrame(candidate_rows)

    # Grade shortlist
    df_filtered = df[df["Grade"].astype(str).apply(lambda g: grade_matches(g, selected_grade))]

    # Merge Talent Cards (display only)
    refresh_talent_cards_session()
    talent_df = st.session_state.get('talent_cards', pd.DataFrame())
    if not talent_df.empty and "Employee ID" in df_filtered.columns and "Employee ID" in talent_df.columns:
        df_filtered = df_filtered.merge(
            talent_df.drop(columns=[c for c in ["DB_ID"] if c in talent_df.columns]),
            on="Employee ID", how="left", suffixes=("", "_TalentCard")
        )

    # Education shortlist
    if "Education" not in df_filtered.columns:
        st.error("People Model must include an 'Education' column for discipline matching.")
        return
    edu_shortlist_df, rule_flags = shortlist_by_education_people_model(
        df_filtered, people_edu_col="Education", jd_edu_text=jd_education
    )
    if edu_shortlist_df.empty:
        st.warning("⚠️ No candidates matched the JD education requirement.")
        return
    st.caption(f"JD education rule parsed → {rule_flags}")

    # Scoring (unchanged)
    def score_row(row):
        score = 0
        def norm(val): return str(val).strip().lower() if val else ""
        try:
            if isinstance(row.get("KPI"), str):
                numbers = re.findall(r"(\d{4}):\s*([\d.]+)", row["KPI"])
                kpis = [float(v) for _, v in numbers]
                if kpis and sum(kpis) / len(kpis) >= 80:
                    score += 3
            elif float(row.get("KPI", 0)) >= 80:
                score += 3
        except:
            pass
        if norm(row.get("Critical Role (Yes/No)")) == "yes":
            score += 1
        years_exp = None
        if "Years of Experience" in row and row["Years of Experience"] not in [None, "", "Not provided"]:
            try:
                years_exp = float(row["Years of Experience"])
            except:
                years_exp = None
        elif "Date Hired" in row and row["Date Hired"] not in [None, "", "Not provided"]:
            try:
                hired_year = int(str(row["Date Hired"])[:4])
                years_exp = datetime.now().year - hired_year
            except:
                years_exp = None
        if years_exp is not None:
            if years_exp > 10:
                score += 3
            elif years_exp > 5:
                score += 2
        return score

    edu_shortlist_df["Succession Score"] = edu_shortlist_df.apply(score_row, axis=1)

    show_cols = [
        "Name", "Grade","Job Title", "KPI", "Succession Score", "Years of Experience",
        "Basic Salary", "Basic Salary x15%", "Planned Retirement", "Date Demob", "Project Name"
    ]

    if "Basic Salary" in edu_shortlist_df.columns:
        def salary_x15(val):
            try: return round(float(val) * 1.15, 2)
            except: return "Not provided"
        edu_shortlist_df["Basic Salary x15%"] = edu_shortlist_df["Basic Salary"].apply(salary_x15)
    else:
        edu_shortlist_df["Basic Salary"] = "Not provided"
        edu_shortlist_df["Basic Salary x15%"] = "Not provided"
    for col in ["Planned Retirement","Date Demob","Project Name"]:
        if col not in edu_shortlist_df.columns: edu_shortlist_df[col] = "Not provided"
    for col in [
        "Strengths","Achievements","Professional Certifications","Education",
        "Work Experience","Skills/Expertise","Leadership","Awards","Career Goals",
        "Job Preferences","Project Experience"
    ]:
        if col not in edu_shortlist_df.columns: edu_shortlist_df[col] = "Not provided"
    for col in ["Planned Retirement", "Date Demob", "Date Joined", "Permanent Date", "Retirement Date"]:
        if col in edu_shortlist_df.columns:
            edu_shortlist_df[col] = edu_shortlist_df[col].apply(format_date)

    st.dataframe(edu_shortlist_df[[c for c in show_cols if c in edu_shortlist_df.columns]])

    fields_to_keep = show_cols + [
        "Critical Role (Yes/No)", "Successor (Yes/No)", "Career Goal",
        "Historical Job Data", "Medical Information/Records", "Overseas Experience", "Date Hired"
    ]
    ai_fields = [col for col in fields_to_keep if col in edu_shortlist_df.columns]
    df_short = edu_shortlist_df[ai_fields].reset_index(drop=True)

    for col in ["Historical Job Data", "KPI"]:
        if col in df_short.columns:
            df_short[col] = df_short[col].astype(str).str.slice(0, 200)
    for col in ai_fields:
        df_short[col] = df_short[col].fillna("Not provided").replace("", "Not provided")

    BATCH_SIZE = 3
    num_candidates = len(df_short)
    num_batches = math.ceil(num_candidates / BATCH_SIZE)
    all_ai_results = []

    st.markdown("### 🤖 AI Recommendations")
    if st.button("Generate with AI"):
        with st.spinner("Analyzing with AI in batches..."):
            raw_chunks = []
            for i in range(num_batches):
                batch_df = df_short.iloc[i*BATCH_SIZE : (i+1)*BATCH_SIZE]
                prompt = f"""
You are an expert HR succession planning advisor.

Given the following job description and project-specific requirements:
---
Job Description:
{jd_content}

Project/Position Profile:
Project: {project_name}
Role: {selected_job_title} ({selected_job_text})
Grade: {selected_grade}
Budget: {budget}
Competency Requirement: {selected_competencies}
---

And the list of candidate profiles (each with a 'Succession Score' already calculated, and including detailed Talent Card info if available):
{json.dumps(batch_df.to_dict(orient='records'), indent=2)}

For each candidate:
- Use all provided information (Succession Score, KPI, Years of Experience, Basic Salary, strengths, achievements, professional certifications, education, work experience, skills/expertise, leadership, awards, career goals, job preferences, project experience, etc.) for your reasoning.
- Predict their readiness for the role as one of: "Ready Now", "Ready in 1-2 Years", "Ready in More Than 2 Years"
- ALWAYS provide a detailed explanation in the "AI Reasoning" column, referencing any fields that are not "Not provided".
- If some fields are not available, use all other available information.

Provide a ranked table in markdown:
| Rank | Name | Job Title | Succession Score | Predicted Readiness | AI Reasoning |
- Every row MUST have an "AI Reasoning" value. No empty cells.
- If a field is missing, use "Not provided" in the table.
❗ Do not include any explanations, summaries, or duplicate tables outside the markdown table.
"""
                messages = [
                    {"role": "system", "content": "You are a helpful assistant that outputs only one markdown table."},
                    {"role": "user", "content": prompt}
                ]
                try:
                    ai_text = dbx_chat(messages=messages, model=st.session_state.get("model_endpoint", DEFAULT_ENDPOINT))
                except Exception as e:
                    st.error(f"Databricks model call failed: {e}")
                    return

                raw_chunks.append(ai_text)

                lines = ai_text.splitlines()
                table_lines = []
                header_seen = False
                for ln in lines:
                    if "|" in ln:
                        if re.search(r"\|\s*-{2,}\s*\|", ln):
                            continue
                        table_lines.append(ln.strip())
                        header_seen = True
                    elif header_seen:
                        break

                if len(table_lines) >= 2:
                    split_lines = [line.split("|")[1:-1] for line in table_lines]
                    headers = [col.strip() for col in split_lines[0]]
                    rows = [[cell.strip() for cell in r] for r in split_lines[1:] if len(r) == len(headers)]
                    if rows:
                        batch_df_ai = pd.DataFrame(rows, columns=headers)
                        all_ai_results.append(batch_df_ai)

            if all_ai_results:
                df_ai = pd.concat(all_ai_results, ignore_index=True)
                shortlist_set = set((str(row.get("Name","")), str(row.get("Job Title",""))) for _, row in df_short.iterrows())
                df_ai = df_ai[df_ai.apply(lambda row: (str(row.get("Name","")), str(row.get("Job Title",""))) in shortlist_set, axis=1)].reset_index(drop=True)
                df_ai = df_ai.drop_duplicates(subset=["Name", "Job Title"], keep="first").reset_index(drop=True)
                if "AI Reasoning" not in df_ai.columns:
                    df_ai["AI Reasoning"] = "No reasoning returned by AI. Please check prompt/data."
                df_ai["AI Reasoning"] = df_ai["AI Reasoning"].replace("", "No reasoning provided by AI.")
                st.subheader("✅ AI Recommended Successors")
                st.dataframe(df_ai)
                excel_bytes = io.BytesIO()
                with pd.ExcelWriter(excel_bytes, engine="xlsxwriter") as writer:
                    df_ai.to_excel(writer, index=False, sheet_name="AI Recommendations")
                st.download_button("⬇️ Download as Excel", data=excel_bytes.getvalue(), file_name="ai_succession_recommendations.xlsx")
            else:
                st.warning("Model did not return a parseable markdown table.")
                with st.expander("🔎 Raw model responses"):
                    for j, chunk in enumerate(raw_chunks, 1):
                        st.markdown(f"**Chunk {j}**")
                        st.code(chunk)

# =========================
# ANALYTICS PAGE
# =========================
def _load_table_json_rows(conn, table: str) -> pd.DataFrame:
    try:
        cur = conn.execute(f"SELECT data FROM {table}")
        rows = [json.loads(r[0]) for r in cur.fetchall()]
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def _bar(df, x, y):
    if _HAS_PLOTLY:
        fig = px.bar(df, x=x, y=y)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.bar_chart(df.set_index(x)[y])

def _first_present_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower()
        if key in lower_map:
            return lower_map[key]
    return None

def analytics_page():
    st.header("📊 Analytics")

    conn = sqlite3.connect(DB_FILE)
    df_pos = _load_table_json_rows(conn, "position_profiles")
    df_tal = _load_table_json_rows(conn, "candidates")
    conn.close()

    st.subheader("📄 Position Profiles")
    if not df_pos.empty:
        pt_col = _first_present_col(df_pos, ["Position Title", "Position  Title", "position title", "position  title"])
        pj_col = _first_present_col(df_pos, ["Projek", "Project", "project"])
        pg_col = _first_present_col(df_pos, ["Position Grade", "Grade", "position grade", "grade"])

        if pt_col and pt_col != "Position Title":
            df_pos.rename(columns={pt_col: "Position Title"}, inplace=True)
        if pj_col and pj_col != "Projek":
            df_pos.rename(columns={pj_col: "Projek"}, inplace=True)
        if pg_col and pg_col != "Position Grade":
            df_pos.rename(columns={pg_col: "Position Grade"}, inplace=True)

        n_positions = int(len(df_pos))

        if "Projek" in df_pos.columns:
            projek_clean = (
                df_pos["Projek"]
                .astype(str)
                .str.strip()
                .replace({"": None, "nan": None, "None": None})
                .dropna()
            )
            n_projects = int(projek_clean.nunique())
        else:
            n_projects = 0

        if "Position Grade" in df_pos.columns:
            grade_clean = (
                df_pos["Position Grade"]
                .astype(str)
                .str.strip()
                .replace({"": None, "nan": None, "None": None})
                .dropna()
            )
            n_grades = int(grade_clean.nunique())
        else:
            n_grades = 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Number of Position", f"{n_positions:,}")
        c2.metric("Number of Project", f"{n_projects:,}")
        c3.metric("Number of Position Grade", f"{n_grades:,}")

        if "Projek" in df_pos.columns:
            st.markdown("**Positions by Project**")
            vc_proj = (
                df_pos["Projek"].astype(str).str.strip()
                .replace({"": None, "nan": None, "None": None})
                .dropna()
                .value_counts()
                .reset_index()
            )
            vc_proj.columns = ["Project", "Count"]
            _bar(vc_proj, "Project", "Count")

        if "Position Grade" in df_pos.columns:
            st.markdown("**Positions by Grade**")
            vc_grade = (
                df_pos["Position Grade"].astype(str).str.strip()
                .replace({"": None, "nan": None, "None": None})
                .dropna()
                .value_counts()
                .reset_index()
            )
            vc_grade.columns = ["Position Grade", "Count"]
            _bar(vc_grade, "Position Grade", "Count")
    else:
        st.info("No Position Profiles found. Please upload **Position Profiles (Excel)** first.")

    st.divider()
    st.subheader("👥 Talent Profiles (People Model Data)")

    if not df_tal.empty:
        grade_col = _first_present_col(df_tal, ["Grade", "Gred", "grade", "gred"])

        if "Employee ID" in df_tal.columns:
            n_employees = df_tal["Employee ID"].astype(str).str.strip().nunique()
        else:
            n_employees = len(df_tal)
        n_gred = df_tal[grade_col].astype(str).str.strip().nunique() if grade_col else 0

        c1, c2 = st.columns(2)
        c1.metric("Number of Employee", f"{n_employees:,}")
        c2.metric("Number of gred", f"{n_gred:,}")

        if grade_col:
            st.markdown("**Employees by Grade (Gred)**")
            vc = (
                df_tal.assign(_grade=df_tal[grade_col].astype(str).str.strip())
                     .query("_grade != ''")
                     ._grade.value_counts()
                     .reset_index()
            )
            vc.columns = ["Grade", "Count"]
            _bar(vc, "Grade", "Count")
        else:
            st.caption("No `Grade/Gred` column found in People Model data.")
    else:
        st.info("No People Model data found. Please upload **People Model Data (Excel)** first.")

# ==========================================================
# NEW PAGE: PERSON ➜ POSITION (JD-only shortlist by grade+education)
# ==========================================================
def _extract_json_block(txt: str) -> Optional[dict]:
    """Try to safely pull a JSON object from raw LLM text (handles code fences & extra prose)."""
    if not isinstance(txt, str) or not txt.strip():
        return None
    m = re.search(r"```json\s*(\{[\s\S]+?\})\s*```", txt, flags=re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"```\s*(\{[\s\S]+?\})\s*```", txt, flags=re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"(\{[\s\S]+\})", txt)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None

def _parse_markdown_table(txt: str) -> Optional[pd.DataFrame]:
    if not isinstance(txt, str) or "|" not in txt:
        return None
    lines = [ln.strip() for ln in txt.splitlines() if "|" in ln]
    if len(lines) < 2:
        return None
    rows = []
    for ln in lines:
        if re.fullmatch(r"\|\s*-{2,}.*", ln):
            continue
        cells = [c.strip() for c in ln.split("|")[1:-1]]
        rows.append(cells)
    if len(rows) < 2:
        return None
    header, body = rows[0], rows[1:]
    body = [r for r in body if len(r) == len(header)]
    if not body:
        return None
    try:
        return pd.DataFrame(body, columns=header)
    except Exception:
        return None

def person_to_position_page():
    st.header("👤 ➜ 🧭 Person ➜ Position Recommendation")

    # People model and JD are required
    conn = sqlite3.connect(DB_FILE)
    candidate_rows = [json.loads(r[0]) for r in conn.execute("SELECT data FROM candidates").fetchall()]
    jd_df = pd.read_sql_query("SELECT position, job_title, grade, filepath, content FROM job_descriptions", conn)
    conn.close()

    if not candidate_rows:
        st.warning("⚠️ No People Model data found. Please upload People Model Data (Excel).")
        return
    if jd_df.empty:
        st.warning("⚠️ No Job Descriptions found. Please upload JD PDFs.")
        return

    refresh_talent_cards_session()
    talent_df = st.session_state.get("talent_cards", pd.DataFrame())
    people_df = pd.DataFrame(candidate_rows)

    # Employee filter (remains)
    name_col = None
    for cand in ["Name", "Employee Name", "Nama"]:
        if cand in people_df.columns:
            name_col = cand
            break
        lower_map = {c.lower(): c for c in people_df.columns}
        if cand.lower() in lower_map:
            name_col = lower_map[cand.lower()]
            break
    if not name_col:
        st.error("People Model must include a 'Name' column.")
        return

    st.subheader("Filter Employee")
    col_a, col_b = st.columns([2, 1])
    with col_a:
        all_names = sorted(people_df[name_col].astype(str).unique().tolist())
        q = st.text_input("Type to filter names", "")
        filtered_names = [n for n in all_names if q.strip().lower() in n.lower()] if q.strip() else all_names
        if not filtered_names:
            st.info("No names match your filter. Clear the search box.")
            return
        selected_name = st.selectbox("Select Employee", filtered_names, index=0)
    with col_b:
        # this is only a preview control; not used for the AI call
        preview_max = max(1, len(jd_df))  # jd_df is already loaded above
        st.number_input(
            "Top N positions (AI)",
            min_value=1,
            max_value=preview_max,
            value=min(5, preview_max),
            step=1,
            key="preview_topk",
        )

    # Get selected candidate profile
    cand_row = people_df[people_df[name_col].astype(str) == str(selected_name)].iloc[0]

    def _get(colnames: list[str], default="Not provided"):
        for c in colnames:
            if c in people_df.columns:
                return str(cand_row.get(c, default))
            lower_map = {x.lower(): x for x in people_df.columns}
            if c.lower() in lower_map:
                return str(cand_row.get(lower_map[c.lower()], default))
        return default

    cand_profile = {
        "Name": selected_name,
        "Employee ID": _get(["Employee ID", "Staff ID", "IC", "NRIC"]),
        "Grade": _get(["Grade", "Gred"]),
        "Job Title": _get(["Job Title", "Title", "Position"]),
        "Education": _get(["Education", "Academic"]),
        "Years of Experience": _get(["Years of Experience", "Experience (Years)", "Yrs Experience"]),
        "KPI": _get(["KPI", "KPI (%)", "KPI Score"]),
        "Career Goal": _get(["Career Goal", "Career Goals", "Job Preference"]),
    }

    # Merge Talent Card details if Employee ID matches
    if not talent_df.empty and "Employee ID" in talent_df.columns and cand_profile["Employee ID"] != "Not provided":
        tc = talent_df[talent_df["Employee ID"].astype(str) == cand_profile["Employee ID"]]
        if not tc.empty:
            tc_row = tc.iloc[0].to_dict()
            for key in [
                "Strengths","Achievements","Professional Certifications","Education",
                "Work Experience","Skills/Expertise","Leadership","Awards","Career Goals",
                "Job Preferences","Project Experience","Date Joined","Permanent Date","Retirement Date"
            ]:
                if key in tc_row and str(tc_row[key]).strip():
                    cand_profile[f"TalentCard::{key}"] = str(tc_row[key])

    st.markdown(
        f"**Employee:** {cand_profile.get('Name','')}  \n"
        f"**Grade:** {cand_profile.get('Grade','')}  \n"
        f"**Current Title:** {cand_profile.get('Job Title','')}  \n"
        f"**Education:** {cand_profile.get('Education','')[:220]}  \n"
        f"**KPI:** {cand_profile.get('KPI','')}"
    )

    # ------- JD-only shortlist by (Grade AND Education) -------
    candidate_grade = cand_profile.get("Grade", "")
    candidate_edu = cand_profile.get("Education", "")
    cand_buckets = education_to_buckets(candidate_edu)

    shortlist_rows = []
    for _, jd in jd_df.iterrows():
        jd_path = jd.get("filepath") or ""
        if not jd_path or not os.path.isfile(jd_path):
            continue

        jd_title = jd.get("job_title") or position_from_filename(jd_path)
        jd_grade = jd.get("grade") or ""
        jd_content = jd.get("content") or ""

        edu_req_text = extract_education_from_jd(jd_content)
        rule = jd_required_disciplines(edu_req_text)

        grade_ok = True if not str(jd_grade).strip() else grade_matches(candidate_grade, str(jd_grade))
        edu_ok = matches_requirement(cand_buckets, rule)

        if not (grade_ok and edu_ok):
            continue

        comp_text = extract_competencies_from_jd(jd_content)

        # keep a private score only for sorting (not displayed/exported)
        score = 1.0
        if str(jd_grade).strip():
            score += 0.1
        tc_skills = cand_profile.get("TalentCard::Skills/Expertise", "")
        if tc_skills and comp_text and (len(set(_slug(tc_skills).split()) & set(_slug(comp_text).split())) >= 3):
            score += 0.1

        shortlist_rows.append({
            "Position Title": jd_title,
            "JD Grade": jd_grade if jd_grade else "Not specified",
            "Education Requirement (JD)": edu_req_text if edu_req_text else "Not specified",
            "Competency Requirement (JD)": comp_text if comp_text else "Not specified",
            "_Score": round(score, 3),         # internal only for sorting
            "JD Path": jd_path,
            "JD Content": jd_content
        })

    if not shortlist_rows:
        st.warning("No JD matched this employee on BOTH grade and education.")
        return

    shortlist_df = (
        pd.DataFrame(shortlist_rows)
          .sort_values("_Score", ascending=False)
          .reset_index(drop=True)
    )

    st.subheader(f"Shortlisted Positions (JD-matched) for: {cand_profile.get('Name','')}")
    # Show without Project and Score
    st.dataframe(
        shortlist_df[["Position Title","JD Grade"]],
        use_container_width=True, hide_index=True
    )

    # Export CSV without Project/Score
    export_cols = ["Position Title","JD Grade","Education Requirement (JD)","Competency Requirement (JD)","JD Path","JD Content"]
    st.download_button(
        "⬇️ Download Shortlist (CSV)",
        data=shortlist_df[export_cols].to_csv(index=False).encode("utf-8"),
        file_name=f"jd_shortlist_for_{_slug(cand_profile.get('Name','unknown'))}.csv",
        mime="text/csv"
    )

    # ------- AI Recommendation & Ranking using JD + People Model + Talent Card -------
    st.subheader("🤖 AI Recommendation (Llama4 Mavericks)")
    with st.expander("Generate AI reasoning for the shortlist"):
        default_ep = st.session_state.get("model_endpoint", DEFAULT_ENDPOINT)
        ep = st.text_input("Serving Endpoint name", value=default_ep, help="e.g., databricks-llama-4-maverick")
        st.session_state["model_endpoint"] = ep

        max_k = len(shortlist_df)
        k = int(
            st.number_input(
                "How many top positions to include for AI?",
                min_value=1,
                max_value=max_k,                      # cannot exceed the shortlist size
                value=min(5, max_k),                  # sensible default; always valid
                step=1,
            )
        )

        if st.button("Generate AI Recommendation", type="primary"):
            # Prepare top-K payload (no project / no score)
            top_jds = shortlist_df.head(k).to_dict(orient="records")
            jd_payload = []
            for item in top_jds:
                jd_payload.append({
                    "position_title": item["Position Title"],
                    "jd_grade": item["JD Grade"],
                    "education_requirement": item["Education Requirement (JD)"],
                    "competency_requirement": item["Competency Requirement (JD)"],
                    "jd_content": item["JD Content"][:4000],
                })

            # Flatten talent card keys
            person_payload = dict(cand_profile)
            for kkey in list(person_payload.keys()):
                if kkey.startswith("TalentCard::"):
                    person_payload[kkey.replace("TalentCard::","TC_")] = person_payload.pop(kkey)

            prompt = f"""
You are an experienced HR talent advisor for a power generation company.
Rank the best roles for ONE employee using the provided Job Descriptions and the employee's talent profile (People Model + Talent Card).

STRICT OUTPUT REQUIREMENTS:
- Return ONLY one JSON object (no markdown, no code fences, no prose around it).
- The JSON MUST have exactly {k} items in "recommendations" (one per input JD), sorted best to worst.
- Each item MUST have fields: "rank" (1..{k}), "position_title", "fit_reason", "risks", "development_plan", "ai_reasoning".
- "ai_reasoning" must be a detailed paragraph (4–8 sentences) that cites concrete matches/mismatches between JD and the person (grade, education discipline, strengths, certifications, work experience, leadership, KPI, etc.).
- If any information is missing, say so and proceed.

EMPLOYEE (People Model + Talent Card excerpts):
{json.dumps(person_payload, ensure_ascii=False)}

TOP-{k} JOB DESCRIPTIONS (already pre-filtered to match this employee on grade+education):
{json.dumps(jd_payload, ensure_ascii=False)}
"""
            messages = [
                {"role": "system", "content": "You are a precise HR advisor. Always return a single valid JSON object exactly as requested."},
                {"role": "user", "content": prompt},
            ]

            try:
                raw = dbx_chat(messages=messages, model=st.session_state.get("model_endpoint", DEFAULT_ENDPOINT))
            except Exception as e:
                st.error(f"Databricks model call failed: {e}")
                return

            obj = None
            try:
                obj = json.loads(raw)
            except Exception:
                obj = _extract_json_block(raw)

            if isinstance(obj, dict) and isinstance(obj.get("recommendations"), list):
                df_ai = pd.DataFrame(obj["recommendations"])
                for col in ["rank","position_title","fit_reason","risks","development_plan","ai_reasoning"]:
                    if col not in df_ai.columns:
                        df_ai[col] = ""
                if "rank" in df_ai.columns:
                    try:
                        df_ai["rank"] = pd.to_numeric(df_ai["rank"], errors="coerce")
                        df_ai = df_ai.sort_values("rank").reset_index(drop=True)
                    except Exception:
                        pass
                st.success("AI recommendations")
                st.dataframe(
                    df_ai[["rank","position_title","fit_reason","risks","development_plan","ai_reasoning"]],
                    use_container_width=True, hide_index=True
                )
                    # --- Export to Excel (JSON path) ---
                df_export = df_ai[["rank","position_title","fit_reason","risks","development_plan","ai_reasoning"]].copy()
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                    df_export.to_excel(writer, index=False, sheet_name="AI_Recommendations")

                file_name = f"ai_recommendations_{_slug(cand_profile.get('Name','unknown'))}.xlsx"
                st.download_button(
                    label="⬇️ Download AI Recommendations (Excel)",
                    data=excel_buffer.getvalue(),
                    file_name=file_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )                  
                    

                if obj.get("summary"):
                    st.write("**AI Summary**")
                    st.write(obj["summary"])
            else:
                df_table = _parse_markdown_table(raw)
                if isinstance(df_table, pd.DataFrame) and not df_table.empty:
                    rename_map = {c.lower(): c for c in df_table.columns}
                    def getcol(*names):
                        for n in names:
                            if n in df_table.columns: return n
                            if n.lower() in rename_map: return rename_map[n.lower()]
                        return None
                    ar_col = getcol("ai_reasoning","AI Reasoning","reasoning","rationale")
                    if not ar_col:
                        df_table["ai_reasoning"] = ""
                    else:
                        df_table.rename(columns={ar_col: "ai_reasoning"}, inplace=True)
                    if "rank" not in df_table.columns:
                        df_table["rank"] = range(1, len(df_table)+1)
                    # drop any 'project' column if model provided it
                    proj_col = getcol("project","Project")
                    if proj_col:
                        df_table.drop(columns=[proj_col], inplace=True, errors="ignore")
                    keep = [c for c in ["rank","position_title","fit_reason","risks","development_plan","ai_reasoning"] if c in df_table.columns]
                    st.info("Model returned a table; parsed successfully.")
                    st.dataframe(df_table[keep], use_container_width=True, hide_index=True)

                            # --- Export to Excel (table fallback path) ---
                    df_export = df_table[keep].copy()
                    excel_buffer = io.BytesIO()
                    with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                        df_export.to_excel(writer, index=False, sheet_name="AI_Recommendations")

                    file_name = f"ai_recommendations_{_slug(cand_profile.get('Name','unknown'))}.xlsx"
                    st.download_button(
                        label="⬇️ Download AI Recommendations (Excel)",
                        data=excel_buffer.getvalue(),
                        file_name=file_name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

                else:
                    st.warning("Raw AI output (non-JSON/non-table):")
                    st.code(raw)

# =========================
# APP ENTRY
# =========================
init_db()
st.set_page_config(page_title="AI Powered HR People Modelling", layout="wide")

# Storage info
with st.sidebar.expander("Storage"):
    st.caption(STORAGE_NOTE)
    st.caption(f"DB: `{DB_FILE}`")
    st.caption(f"JDs: `{JD_FOLDER}`")

# 🔐 Require login
_login_gate("AI Powered HR People Modelling")

st.sidebar.title("Navigation")
pages = [
    "Analytics",
    "Succession Recommendation (Home)",
    "Person ➜ Position Recommendation",
    "Upload Job Descriptions",
    "Upload People Model Data",
    "Upload Position Profiles",
    "Upload Talent Card(s)",
]
page = st.sidebar.radio("Go to", pages, index=0)

if page == "Analytics":
    analytics_page()
elif page == "Succession Recommendation (Home)":
    succession_recommendation()
elif page == "Person ➜ Position Recommendation":
    person_to_position_page()
elif page == "Upload Job Descriptions":
    upload_job_descriptions()
elif page == "Upload People Model Data":
    upload_people_model()
elif page == "Upload Position Profiles":
    upload_position_profiles()
elif page == "Upload Talent Card(s)":
    upload_talent_cards()
