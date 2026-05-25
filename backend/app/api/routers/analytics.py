from collections import Counter
import json

from fastapi import APIRouter

from ...data.db import get_db_connection, log_audit

router = APIRouter()


def _first_non_empty(d: dict, keys: list[str]) -> str:
    for key in keys:
        val = d.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


@router.get("/summary")
def get_analytics_summary():
    conn = get_db_connection()
    try:
        rows_pos = conn.execute("SELECT data FROM position_profiles").fetchall()
        rows_tal = conn.execute("SELECT data FROM candidates").fetchall()
    except Exception:
        rows_pos = []
        rows_tal = []
    finally:
        conn.close()

    pos_data = []
    for r in rows_pos:
        try:
            pos_data.append(json.loads(r["data"]))
        except Exception:
            pass

    tal_data = []
    for r in rows_tal:
        try:
            tal_data.append(json.loads(r["data"]))
        except Exception:
            pass

    projects = Counter()
    position_grades = Counter()
    employee_grades = Counter()

    for item in pos_data:
        project = _first_non_empty(item, ["Projek", "Project", "project"])
        if project:
            projects[project] += 1
        grade = _first_non_empty(item, ["Position Grade", "Grade", "grade"])
        if grade:
            position_grades[grade] += 1

    for item in tal_data:
        grade = _first_non_empty(item, ["Grade", "Gred", "grade"])
        if grade:
            employee_grades[grade] += 1

    log_audit("view_summary", "analytics", "dashboard", None, f"positions={len(pos_data)}; employees={len(tal_data)}", "success")
    return {
        "positions": {
            "total": len(pos_data),
            "distinct_projects": len(projects),
            "distinct_grades": len(position_grades),
        },
        "employees": {
            "total": len(tal_data),
            "distinct_grades": len(employee_grades),
        },
        "charts": {
            "positions_by_project": [
                {"name": name, "value": value} for name, value in projects.most_common(12)
            ],
            "employees_by_grade": [
                {"name": name, "value": value} for name, value in employee_grades.most_common(12)
            ],
        },
    }
