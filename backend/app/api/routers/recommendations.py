from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import pandas as pd
import json
import os
import math
import re
import tempfile
from pathlib import Path

try:
    from databricks.sdk import WorkspaceClient
except Exception:
    WorkspaceClient = None

from ...data.db import get_db_connection, fetch_dataframe, log_audit
from ...services import parser, scoring, llm

router = APIRouter()


def _get_workspace_client():
    if WorkspaceClient is None:
        return None
    try:
        return WorkspaceClient()
    except Exception:
        return None


def _download_volume_file_to_local(path_str: str) -> str | None:
    if not str(path_str).startswith("/Volumes/"):
        return path_str
    path = Path(path_str)
    if path.exists() and path.is_file():
        return str(path)
    w = _get_workspace_client()
    if w is None:
        return None
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=path.suffix)
    tmp.close()
    try:
        resp = w.files.download(path_str)
        if resp is None or resp.contents is None:
            return None
        with open(tmp.name, "wb") as f:
            f.write(resp.contents.read())
            f.flush()
            os.fsync(f.fileno())
        return tmp.name
    except Exception:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass
        return None


def _read_jd_content_from_path(path_str: str) -> str:
    if not path_str:
        return ""
    local_copy = None
    try:
        local_copy = _download_volume_file_to_local(path_str)
        if not local_copy:
            return ""
        path = Path(local_copy)
        if not path.exists() or not path.is_file():
            return ""
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            import PyPDF2
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                parts = []
                for page in reader.pages[:20]:
                    try:
                        page_text = page.extract_text() or ""
                    except Exception:
                        page_text = ""
                    if page_text:
                        parts.append(page_text)
                return "\n".join(parts)[:40000]
        return path.read_text(encoding="utf-8", errors="ignore")[:40000]
    except Exception:
        return ""
    finally:
        if local_copy and local_copy != path_str:
            try:
                Path(local_copy).unlink(missing_ok=True)
            except Exception:
                pass


def _best_jd_content(db_content: str, filepath: str) -> str:
    file_content = _read_jd_content_from_path(filepath)
    if file_content.strip():
        return file_content
    return db_content or ""


def _safe_text(value, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", parser._slug(value or "")).strip()


def _title_overlap_score(a: str, b: str) -> int:
    left = set(_normalize_title(a).split())
    right = set(_normalize_title(b).split())
    if not left or not right:
        return 0
    return len(left & right)


GENERIC_POSITION_TOKENS = {
    "senior", "principal", "lead", "chief", "assistant", "associate", "engineer", "manager",
    "executive", "officer", "specialist", "project", "position", "job", "jd", "description",
    "hq", "site",
}


def _tokenize(value: str) -> list[str]:
    normalized = _normalize_title(value)
    return [token for token in normalized.split() if token]


def _project_tokens(project_name: str) -> set[str]:
    aliases = {
        "nhep": {"nhep", "nenggiri"},
        "nenggiri": {"nenggiri", "nhep"},
        "hhfs": {"hhfs"},
        "hess": {"hess"},
    }
    tokens = {token for token in _tokenize(project_name) if token not in {"projek", "project"}}
    expanded = set()
    for token in tokens:
        expanded.update(aliases.get(token, {token}))
    return expanded


def _grade_tokens(value: str) -> set[str]:
    return set(re.findall(r"(?:CM|GM|M|E)\d{2}", str(value or "").upper()))


SPECIALTY_TOKEN_ALIASES = {
    "scheduler": {"scheduler", "schedule", "scheduling", "planner", "planning"},
    "schedule": {"schedule", "scheduler", "scheduling", "planner", "planning"},
    "scheduling": {"scheduling", "scheduler", "schedule", "planner", "planning"},
    "planner": {"planner", "planning", "schedule", "scheduler", "scheduling"},
    "planning": {"planning", "planner", "schedule", "scheduler", "scheduling"},
}


def _token_matches(token: str, target_tokens: set[str]) -> bool:
    aliases = SPECIALTY_TOKEN_ALIASES.get(token, {token})
    return any(alias in target_tokens for alias in aliases)


def _specialty_tokens(value: str) -> list[str]:
    tokens = []
    for token in _tokenize(value):
        if token in GENERIC_POSITION_TOKENS:
            continue
        if re.match(r"^[em]\d{2}$", token):
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _position_match_text(position_title: str, position_text: str = "") -> str:
    title = _safe_text(position_title)
    detail = _safe_text(position_text)
    if not detail or _normalize_title(detail) in _normalize_title(title):
        return title
    return " - ".join([part for part in [title, detail] if part])


def _jd_match_score(row: dict, position_title: str, position_grade: str, project_name: str = "", position_text: str = "") -> float:
    jd_title = row.get("job_title") or row.get("position") or parser.position_from_filename(row.get("filepath") or "")
    jd_project = parser.normalize_project_from_filename(row.get("original_filename") or row.get("filepath") or "") or ""
    jd_search_text = " ".join([
        _safe_text(jd_title),
        _safe_text(row.get("position")),
        _safe_text(row.get("grade")),
        _safe_text(row.get("original_filename")),
        _safe_text(row.get("filepath")),
        jd_project,
    ])

    base_title = _safe_text(position_title)
    match_text = _position_match_text(position_title, position_text)
    base_title_norm = _normalize_title(base_title)
    match_text_norm = _normalize_title(match_text)
    jd_norm = _normalize_title(jd_search_text)
    base_title_tokens = _tokenize(base_title)
    match_tokens = _tokenize(match_text)
    jd_tokens = set(_tokenize(jd_search_text))
    score = 0.0

    if base_title_norm and base_title_norm in jd_norm:
        score += 60
    if match_text_norm and match_text_norm in jd_norm:
        score += 35

    if base_title_tokens:
        title_overlap = len([token for token in base_title_tokens if token in jd_tokens])
        score += (title_overlap / len(base_title_tokens)) * 30
    else:
        title_overlap = 0

    if match_tokens:
        match_overlap = len([token for token in match_tokens if _token_matches(token, jd_tokens)])
        score += (match_overlap / len(match_tokens)) * 10

    specialty_tokens = []
    for token in _specialty_tokens(position_text):
        if token not in specialty_tokens:
            specialty_tokens.append(token)
    for token in _specialty_tokens(match_text):
        if token not in base_title_tokens and token not in specialty_tokens:
            specialty_tokens.append(token)

    if specialty_tokens:
        specialty_overlap = len([token for token in specialty_tokens if _token_matches(token, jd_tokens)])
        if specialty_overlap:
            score += specialty_overlap * 70
            score += (specialty_overlap / len(specialty_tokens)) * 40
        else:
            score -= 90

    jd_grade_tokens = _grade_tokens(" ".join([_safe_text(row.get("grade")), _safe_text(jd_title), _safe_text(row.get("original_filename"))]))
    position_grade_tokens = _grade_tokens(position_grade)
    if jd_grade_tokens and position_grade_tokens and jd_grade_tokens & position_grade_tokens:
        score += 15
    elif row.get("grade") and position_grade and (scoring.grade_matches(row.get("grade") or "", position_grade) or scoring.grade_matches(position_grade, row.get("grade") or "")):
        score += 10

    project_tokens = _project_tokens(project_name)
    known_project_tokens = {"nhep", "nenggiri", "hhfs", "hess"}
    if project_tokens:
        project_matches = len(project_tokens & jd_tokens)
        if project_matches:
            score += 25 + (project_matches * 5)
        elif jd_tokens & known_project_tokens:
            score -= 15

    if not specialty_tokens and base_title_tokens and title_overlap == len(base_title_tokens):
        score += 10

    return score


def _first_present(row: dict, keys: list[str], fallback: str = "Not provided") -> str:
    for key in keys:
        if key in row and row.get(key) not in [None, ""]:
            return _safe_text(row.get(key), fallback)
    return fallback


def _serialize_candidate_row(row: dict, idx: int) -> dict:
    basic_salary = _first_present(row, ["Basic Salary", "Basic salary", "Salary", "Basic_Salary"])
    salary_x15 = _first_present(row, ["Basic Salary x15%", "Basic Salary x 15%", "Basic Salary 15%", "Basic Salary x15", "Salary x15%", "Salary x 15%"])
    if salary_x15 == "Not provided" and basic_salary not in ["Not provided", ""]:
        try:
            salary_x15 = str(round(float(str(basic_salary).replace(',', '')) * 0.15, 2))
        except Exception:
            salary_x15 = "Not provided"
    return {
        "rank": idx + 1,
        "name": _safe_text(row.get("Name"), "Unknown Candidate"),
        "job_title": _safe_text(row.get("Job Title"), "Not provided"),
        "grade": _safe_text(row.get("Grade"), "Not provided"),
        "education": _safe_text(row.get("Education"), "Not provided"),
        "succession_score": row.get("Succession Score", 0),
        "strengths": _safe_text(row.get("Strengths"), "Not provided"),
        "achievements": _safe_text(row.get("Achievements"), "Not provided"),
        "kpi": _safe_text(row.get("KPI"), "Not provided"),
        "years_of_experience": _first_present(row, ["Years of Experience", "Years Experience", "Experience (Years)"]),
        "basic_salary": basic_salary,
        "basic_salary_x15": salary_x15,
        "planned_retirement": _first_present(row, ["Planned Retirement", "Planned Retirement Date", "Retirement Date"]),
        "date_demob": _first_present(row, ["Date Demob", "Demob Date", "Date of Demob"]),
        "project_name": _first_present(row, ["Project Name", "Project", "Projek"]),
    }


def _prepare_succession_context(position_title: str, position_grade: str, jd_filepath: str, project_name: str = "", position_text: str = ""):
    conn = get_db_connection()
    jd_rows = conn.execute("SELECT id, job_title, grade, filepath, content, original_filename FROM job_descriptions ORDER BY id DESC").fetchall()
    cand_rows = conn.execute("SELECT data FROM candidates").fetchall()
    conn.close()

    if not jd_rows:
        raise HTTPException(status_code=404, detail="No job descriptions found")
    if not cand_rows:
        raise HTTPException(status_code=404, detail="No candidates found")

    selected_jd = None
    fallback_best = None
    fallback_best_score = -1

    for row in jd_rows:
        row_dict = dict(row)
        row_title = row_dict.get("job_title") or parser.position_from_filename(row_dict.get("filepath") or "")
        row_dict["resolved_job_title"] = row_title
        score = _jd_match_score(row_dict, position_title, position_grade, project_name, position_text)
        if score > fallback_best_score:
            fallback_best_score = score
            fallback_best = row_dict
        if str(row_dict.get("filepath") or "") == str(jd_filepath):
            selected_jd = row_dict

    if selected_jd is None:
        raise HTTPException(status_code=404, detail="JD not found in database")

    auto_jd = fallback_best or selected_jd
    active_content = _best_jd_content(selected_jd.get("content") or "", selected_jd.get("filepath") or "")
    auto_content = _best_jd_content(auto_jd.get("content") or "", auto_jd.get("filepath") or "")

    jd_education = parser.extract_education_from_jd(active_content)
    jd_competencies = parser.extract_competencies_from_jd(active_content)
    auto_education = parser.extract_education_from_jd(auto_content)
    auto_rule = scoring.jd_required_disciplines(auto_education)

    df = pd.DataFrame([json.loads(r["data"]) for r in cand_rows])
    if df.empty:
        raise HTTPException(status_code=404, detail="No candidates found")

    if "Grade" not in df.columns:
        df["Grade"] = ""
    df_filtered = df[df["Grade"].astype(str).apply(lambda g: scoring.grade_matches(g, position_grade))].copy()

    if "Education" not in df_filtered.columns:
        raise HTTPException(status_code=400, detail="Candidates must have 'Education' column.")

    mask = []
    for _, row in df_filtered.iterrows():
        buckets = scoring.education_to_buckets(str(row.get("Education", "")))
        mask.append(scoring.matches_requirement(buckets, auto_rule))

    edu_shortlist_df = df_filtered.loc[mask].copy()
    if edu_shortlist_df.empty:
        shortlist = []
    else:
        edu_shortlist_df["Succession Score"] = edu_shortlist_df.apply(lambda r: scoring.compute_succession_score(r.to_dict()), axis=1)
        edu_shortlist_df = edu_shortlist_df.sort_values(by=["Succession Score", "Name"], ascending=[False, True]).reset_index(drop=True)
        shortlist = [_serialize_candidate_row(row.to_dict(), idx) for idx, (_, row) in enumerate(edu_shortlist_df.iterrows())]

    return {
        "selected_jd": {
            "id": selected_jd.get("id"),
            "filepath": selected_jd.get("filepath"),
            "job_title": selected_jd.get("resolved_job_title"),
            "grade": selected_jd.get("grade") or "",
            "original_filename": selected_jd.get("original_filename") or "",
        },
        "auto_detected_jd": {
            "id": auto_jd.get("id"),
            "filepath": auto_jd.get("filepath"),
            "job_title": auto_jd.get("resolved_job_title"),
            "grade": auto_jd.get("grade") or "",
            "original_filename": auto_jd.get("original_filename") or "",
        },
        "jd_content": active_content,
        "jd_education": jd_education,
        "jd_competencies": jd_competencies,
        "rule": auto_rule,
        "shortlist": shortlist,
        "shortlist_df": edu_shortlist_df if not edu_shortlist_df.empty else pd.DataFrame(),
    }


class SuccessionPreviewRequest(BaseModel):
    project_name: str = ""
    position_title: str
    position_text: str = ""
    position_grade: str
    jd_filepath: str


class SuccessionRequest(BaseModel):
    project_name: str
    position_title: str
    position_text: str
    position_grade: str
    budget: str
    jd_filepath: str


@router.post("/succession-preview")
def succession_preview(req: SuccessionPreviewRequest):
    context = _prepare_succession_context(req.position_title, req.position_grade, req.jd_filepath, req.project_name, req.position_text)
    return {
        "selected_jd": context["selected_jd"],
        "auto_detected_jd": context["auto_detected_jd"],
        "shortlist": context["shortlist"],
        "rule": context["rule"],
    }


@router.post("/succession")
def run_succession(req: SuccessionRequest):
    context = _prepare_succession_context(req.position_title, req.position_grade, req.jd_filepath, req.project_name, req.position_text)
    edu_shortlist_df = context["shortlist_df"]
    if edu_shortlist_df.empty:
        log_audit("run_succession", "recommendations", "succession", req.position_title, "No shortlist candidates found", "success")
        return {
            "results": [],
            "rule": context["rule"],
            "shortlist": context["shortlist"],
            "selected_jd": context["selected_jd"],
            "auto_detected_jd": context["auto_detected_jd"],
        }

    BATCH_SIZE = 3
    num_candidates = len(edu_shortlist_df)
    num_batches = math.ceil(num_candidates / BATCH_SIZE)
    all_ai_results = []

    ai_fields = ["Name", "Grade", "Job Title", "KPI", "Succession Score", "Years of Experience", "Strengths", "Achievements", "Education"]
    df_short = edu_shortlist_df[[c for c in ai_fields if c in edu_shortlist_df.columns]].fillna("Not provided").copy()

    for i in range(num_batches):
        batch_df = df_short.iloc[i * BATCH_SIZE : (i + 1) * BATCH_SIZE]
        prompt = f'''
You are an expert HR succession planning advisor.
Job Description:
{context["jd_content"][:3000]}

Project/Position Profile:
Project: {req.project_name}
Role: {req.position_title}
Grade: {req.position_grade}
Competency Requirement: {context["jd_competencies"]}

Candidates:
{json.dumps(batch_df.to_dict(orient="records"), indent=2)}

Predict readiness: "Ready Now", "Ready in 1-2 Years", "Ready in More Than 2 Years".
Return a markdown table: | Rank | Name | Job Title | Succession Score | Predicted Readiness | AI Reasoning |
        '''
        messages = [
            {"role": "system", "content": "You are a helpful assistant that outputs only one markdown table."},
            {"role": "user", "content": prompt},
        ]
        try:
            ai_text = llm.chat(messages)
            lines = [ln.strip() for ln in ai_text.splitlines() if "|" in ln]
            body_lines = [ln for ln in lines if not re.match(r"^\|\s*-{2,}", ln)]
            if len(body_lines) >= 2:
                headers = [c.strip() for c in body_lines[0].split("|")[1:-1]]
                for block in body_lines[1:]:
                    cells = [c.strip() for c in block.split("|")[1:-1]]
                    if len(cells) == len(headers):
                        row_dict = dict(zip(headers, cells))
                        all_ai_results.append(row_dict)
        except Exception as e:
            print("LLM Error:", e)

    log_audit("run_succession", "recommendations", "succession", req.position_title, f"Project={req.project_name}; candidates_returned={len(all_ai_results)}", "success")
    return {
        "results": all_ai_results,
        "rule": context["rule"],
        "shortlist": context["shortlist"],
        "selected_jd": context["selected_jd"],
        "auto_detected_jd": context["auto_detected_jd"],
    }


class PersonToPositionRequest(BaseModel):
    employee_name: str
    top_k: int = 5


@router.post("/person-to-position")
def run_person_to_position(req: PersonToPositionRequest):
    conn = get_db_connection()
    cand_rows = conn.execute("SELECT data FROM candidates").fetchall()
    jd_df = fetch_dataframe("SELECT position, job_title, grade, filepath, content FROM job_descriptions")
    conn.close()

    people_df = pd.DataFrame([json.loads(r["data"]) for r in cand_rows])
    if people_df.empty or "Name" not in people_df.columns or req.employee_name not in people_df["Name"].values:
        raise HTTPException(404, "Employee not found")

    cand_row = people_df[people_df["Name"] == req.employee_name].iloc[0]
    cand_profile = cand_row.to_dict()

    candidate_grade = cand_profile.get("Grade", "")
    candidate_edu = cand_profile.get("Education", "")
    cand_buckets = scoring.education_to_buckets(candidate_edu)

    shortlist_rows = []
    for _, jd in jd_df.iterrows():
        jd_path = jd.get("filepath") or ""
        jd_title = jd.get("job_title") or parser.position_from_filename(jd_path)
        jd_grade = jd.get("grade") or ""
        jd_content = _best_jd_content(jd.get("content") or "", jd_path)

        edu_req_text = parser.extract_education_from_jd(jd_content)
        rule = scoring.jd_required_disciplines(edu_req_text)

        grade_ok = True if not str(jd_grade).strip() else scoring.grade_matches(candidate_grade, jd_grade)
        edu_ok = scoring.matches_requirement(cand_buckets, rule)

        if grade_ok and edu_ok:
            comp_text = parser.extract_competencies_from_jd(jd_content)
            shortlist_rows.append({
                "Position Title": jd_title,
                "JD Grade": jd_grade,
                "Education Requirement (JD)": edu_req_text,
                "Competency Requirement (JD)": comp_text,
                "JD Content": jd_content[:4000],
                "match_score": _title_overlap_score(cand_profile.get("Job Title", ""), jd_title) + (3 if str(jd_grade).strip() and scoring.grade_matches(candidate_grade, jd_grade) else 0),
            })

    if not shortlist_rows:
        log_audit("run_person_to_position", "recommendations", "employee", req.employee_name, "No matching positions found", "success")
        return {"recommendations": []}

    shortlist_rows = sorted(shortlist_rows, key=lambda r: (-r.get("match_score", 0), str(r.get("Position Title", ""))))
    top_jds = shortlist_rows if req.top_k <= 0 else shortlist_rows[: req.top_k]

    prompt = f"""
You are an experienced HR talent advisor.
Rank the best roles for ONE employee. Return ONLY one JSON object with "recommendations": [{{ rank, position_title, fit_reason, risks, development_plan, ai_reasoning }}]

EMPLOYEE: {json.dumps(cand_profile, ensure_ascii=False)}
TOP JOB DESCRIPTIONS: {json.dumps(top_jds, ensure_ascii=False)}
    """

    try:
        raw = llm.chat([{"role": "user", "content": prompt}])
        match = re.search(r"(\{[\s\S]+\})", raw)
        if match:
            obj = json.loads(match.group(1))
            rec_count = len(obj.get("recommendations") or []) if isinstance(obj, dict) else 0
            log_audit("run_person_to_position", "recommendations", "employee", req.employee_name, f"recommendations_returned={rec_count}", "success")
            return obj
    except Exception as e:
        print("LLM error", e)

    log_audit("run_person_to_position", "recommendations", "employee", req.employee_name, "Failed to generate AI recommendation", "failed")
    return {"recommendations": [], "error": "Failed to generate"}
