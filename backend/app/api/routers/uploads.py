import io
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import List

try:
    from databricks.sdk import WorkspaceClient
except Exception:
    WorkspaceClient = None

import pandas as pd
import PyPDF2
from fastapi import APIRouter, BackgroundTasks, Body, File, Form, HTTPException, UploadFile

from ...data.db import (
    fetch_dataframe,
    get_db_connection,
    get_uc_volume_category_dir,
    get_upload_root,
    log_audit,
)
from ...services import parser

router = APIRouter()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
VALID_CATEGORIES = {"job-descriptions", "position-profiles", "people-model", "talent-cards"}


def _read_excel_from_path(file_path: str) -> pd.DataFrame:
    return pd.read_excel(file_path)


def _safe_filename(name: str) -> str:
    return Path(name or "upload.bin").name.replace("..", "_").strip() or "upload.bin"


def _chunks_root() -> Path:
    p = Path(get_upload_root()) / "_chunked"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _chunk_session_dir(upload_id: str) -> Path:
    session = _chunks_root() / upload_id
    session.mkdir(parents=True, exist_ok=True)
    return session


def _assembled_path(upload_id: str, filename: str) -> Path:
    return _chunk_session_dir(upload_id) / _safe_filename(filename)


def _cleanup_chunk_session(upload_id: str) -> None:
    try:
        shutil.rmtree(_chunk_session_dir(upload_id), ignore_errors=True)
    except Exception:
        pass


def _fallback_storage_dir(category: str) -> Path:
    fallback_map = {
        "job-descriptions": Path(get_upload_root()) / "job_descriptions",
        "position-profiles": Path(get_upload_root()) / "position_profiles",
        "talent-cards": Path(get_upload_root()) / "talent_cards",
        "people-model": Path(get_upload_root()) / "talent_profile",
    }
    path = fallback_map[category]
    path.mkdir(parents=True, exist_ok=True)
    return path


def _category_storage_dir(category: str) -> Path:
    preferred = Path(get_uc_volume_category_dir(category))
    try:
        os.makedirs(preferred, exist_ok=True)
        return preferred
    except Exception:
        return _fallback_storage_dir(category)


def _is_volume_path(path_str: str | Path) -> bool:
    try:
        return str(path_str).startswith("/Volumes/")
    except Exception:
        return False


def _can_write_local_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / f".write_test_{uuid.uuid4().hex}"
        with open(test_file, "wb") as f:
            f.write(b"ok")
            f.flush()
            os.fsync(f.fileno())
        test_file.unlink(missing_ok=True)
        return True
    except Exception:
        return False



def _download_volume_file_to_local(path_str: str) -> str | None:
    if not _is_volume_path(path_str):
        return path_str
    local_path = Path(path_str)
    if local_path.exists() and local_path.is_file():
        return str(local_path)
    w = _get_workspace_client()
    if w is None:
        return None
    tmp = None
    try:
        suffix = Path(path_str).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        resp = w.files.download(path_str)
        if resp is None or resp.contents is None:
            return None
        with open(tmp.name, 'wb') as f:
            shutil.copyfileobj(resp.contents, f, length=CHUNK_SIZE)
            f.flush()
            os.fsync(f.fileno())
        if Path(tmp.name).exists() and Path(tmp.name).is_file():
            return tmp.name
    except Exception:
        if tmp is not None:
            try:
                Path(tmp.name).unlink(missing_ok=True)
            except Exception:
                pass
    return None


def _local_readable_copy(path_str: str) -> tuple[str | None, bool]:
    if not path_str:
        return None, False
    local_path = Path(path_str)
    if local_path.exists() and local_path.is_file():
        return str(local_path), False
    downloaded = _download_volume_file_to_local(path_str)
    if downloaded:
        return downloaded, downloaded != path_str
    return None, False


def _cleanup_local_copy(path_str: str | None, should_delete: bool) -> None:
    if should_delete and path_str:
        try:
            Path(path_str).unlink(missing_ok=True)
        except Exception:
            pass

def _get_workspace_client():
    if WorkspaceClient is None:
        return None
    try:
        return WorkspaceClient()
    except Exception:
        return None


def _ensure_volume_dir(directory: str) -> None:
    if not _is_volume_path(directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        return
    w = _get_workspace_client()
    if w is None:
        raise RuntimeError(f"Databricks SDK client unavailable for UC Volume path: {directory}")

    current = ''
    for part in Path(directory).parts:
        if not part:
            continue
        current = f"{current}/{part}" if current else (f"/{part}" if directory.startswith('/') else part)
        if current == '/Volumes':
            continue
        try:
            w.files.create_directory(current)
        except Exception:
            pass


def _volume_file_exists(file_path: str) -> bool:
    if not _is_volume_path(file_path):
        return Path(file_path).exists()
    w = _get_workspace_client()
    if w is None:
        return False
    parent = str(Path(file_path).parent)
    target_name = Path(file_path).name
    try:
        for entry in w.files.list_directory_contents(parent):
            if entry and getattr(entry, 'name', None) == target_name and not getattr(entry, 'is_directory', False):
                return True
    except Exception:
        return False
    return False


def _unique_target_path(directory: Path, filename: str) -> Path:
    safe_name = _safe_filename(filename)
    target = directory / safe_name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    idx = 1
    while True:
        candidate = directory / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1


def _move_file_to_category_storage(src_path: str, category: str, filename: str) -> str:
    preferred_dir = str(get_uc_volume_category_dir(category))
    fallback_dir = str(_fallback_storage_dir(category))
    last_error = None

    candidates = [preferred_dir]
    if preferred_dir != fallback_dir:
        candidates.append(fallback_dir)

    for directory in candidates:
        try:
            target_dir = Path(directory)

            # Keep UC Volume storage as the first choice. When the Databricks volume is
            # mounted into the app container, direct filesystem writes are the most reliable
            # path and preserve the existing storage configuration.
            if _can_write_local_directory(target_dir):
                target = _unique_target_path(target_dir, filename)
                with open(src_path, "rb") as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=CHUNK_SIZE)
                    dst.flush()
                    os.fsync(dst.fileno())
                if target.exists() and target.is_file() and target.stat().st_size > 0:
                    Path(src_path).unlink(missing_ok=True)
                    return str(target)
                last_error = RuntimeError(f"Stored file missing after write: {target}")
                continue

            # If the UC Volume is not available as a regular local filesystem mount,
            # fall back to the Databricks SDK for /Volumes paths only.
            if _is_volume_path(directory):
                _ensure_volume_dir(directory)
                target = _unique_target_path(target_dir, filename)
                w = _get_workspace_client()
                if w is None:
                    last_error = RuntimeError(f"UC Volume upload requires Databricks SDK access for {target}")
                    continue
                try:
                    with open(src_path, 'rb') as src:
                        w.files.upload(str(target), src, overwrite=False)
                    if _volume_file_exists(str(target)):
                        Path(src_path).unlink(missing_ok=True)
                        return str(target)
                    last_error = RuntimeError(f"Stored file missing after SDK upload: {target}")
                    continue
                except Exception as sdk_error:
                    last_error = sdk_error
                    continue

            # Non-volume fallback local storage.
            target_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_target_path(target_dir, filename)
            with open(src_path, "rb") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=CHUNK_SIZE)
                dst.flush()
                os.fsync(dst.fileno())
            if target.exists() and target.is_file() and target.stat().st_size > 0:
                Path(src_path).unlink(missing_ok=True)
                return str(target)
            last_error = RuntimeError(f"Stored file missing after write: {target}")
        except Exception as e:
            last_error = e

    raise HTTPException(status_code=500, detail=f"Failed to store file in configured storage: {last_error}")


def _delete_physical_file(path_str: str | None) -> None:
    if not path_str:
        return
    try:
        if _is_volume_path(path_str):
            w = _get_workspace_client()
            if w is not None:
                try:
                    w.files.delete(path_str)
                    return
                except Exception:
                    pass
        p = Path(path_str)
        if p.exists() and p.is_file():
            p.unlink()
    except Exception:
        pass




def _list_storage_files(category: str, allowed_suffixes: set[str] | None = None) -> list[str]:
    directory = get_uc_volume_category_dir(category)
    suffixes = {x.lower() for x in allowed_suffixes} if allowed_suffixes else None
    files: list[str] = []

    def include(path_str: str) -> bool:
        return not suffixes or Path(path_str).suffix.lower() in suffixes

    try:
        local_dir = Path(directory)
        if local_dir.exists() and local_dir.is_dir():
            for item in local_dir.iterdir():
                if item.is_file() and include(str(item)):
                    files.append(str(item))
            return sorted(set(files), key=lambda x: Path(x).name.lower())
    except Exception:
        pass

    if _is_volume_path(directory):
        w = _get_workspace_client()
        if w is not None:
            try:
                for entry in w.files.list_directory_contents(directory):
                    name = getattr(entry, "name", None)
                    if name and not getattr(entry, "is_directory", False):
                        candidate = str(Path(directory) / name)
                        if include(candidate):
                            files.append(candidate)
            except Exception:
                pass
    return sorted(set(files), key=lambda x: Path(x).name.lower())


def _stored_file_exists(path_str: str | None) -> bool:
    if not path_str:
        return False
    try:
        p = Path(path_str)
        if p.exists() and p.is_file():
            return True
    except Exception:
        pass
    return _volume_file_exists(str(path_str)) if _is_volume_path(str(path_str)) else False


def _sync_file_backed_rows(table: str, category: str, import_func, allowed_suffixes: set[str]) -> dict:
    removed = 0
    imported = 0
    conn = get_db_connection()
    rows = conn.execute(f"SELECT id, data FROM {table}").fetchall()
    existing_paths: set[str] = set()
    for row in rows:
        data = _json_loads_maybe(row["data"])
        source_path = str(data.get("__source_file_path") or "")
        if source_path and _stored_file_exists(source_path):
            existing_paths.add(source_path)
        else:
            conn.execute(f"DELETE FROM {table} WHERE id=?", (row["id"],))
            removed += 1
    conn.commit()
    conn.close()

    for file_path in _list_storage_files(category, allowed_suffixes):
        if file_path not in existing_paths:
            try:
                import_func(file_path, Path(file_path).name)
                imported += 1
            except Exception:
                pass
    if removed or imported:
        log_audit("sync", "uploads", category, None, f"removed_rows={removed}; imported_files={imported}", "success")
    return {"removed_rows": removed, "imported_files": imported}


def _sync_job_descriptions_with_storage() -> dict:
    removed = 0
    imported = 0
    conn = get_db_connection()
    rows = conn.execute("SELECT id, filepath FROM job_descriptions").fetchall()
    existing_paths: set[str] = set()
    for row in rows:
        path = str(row["filepath"] or "")
        if path and _stored_file_exists(path):
            existing_paths.add(path)
        else:
            conn.execute("DELETE FROM job_descriptions WHERE id=?", (row["id"],))
            removed += 1
    conn.commit()
    conn.close()

    for file_path in _list_storage_files("job-descriptions", {".pdf"}):
        if file_path in existing_paths:
            continue
        filename = Path(file_path).name
        position = os.path.splitext(filename)[0].replace("_", " ").replace(" JD", "").strip()
        content = _extract_pdf_text_from_path_limited(file_path)
        if not content:
            content = f"Uploaded PDF: {filename}. No extractable text was found, so metadata was inferred from the filename."
        job_title = parser.extract_field(content, "Title") or parser.position_from_filename(filename)
        grade = parser.extract_field(content, "Grade") or ""
        conn = get_db_connection()
        conn.execute("INSERT INTO job_descriptions (position, job_title, grade, filepath, content, original_filename) VALUES (?, ?, ?, ?, ?, ?)", (position, job_title, grade, file_path, content, filename))
        conn.commit()
        conn.close()
        imported += 1
    if removed or imported:
        log_audit("sync", "uploads", "job-descriptions", None, f"removed_records={removed}; imported_files={imported}", "success")
    return {"removed_records": removed, "imported_files": imported}


def _sync_talent_cards_with_storage() -> dict:
    removed = 0
    imported = 0
    conn = get_db_connection()
    rows = conn.execute("SELECT id, data FROM talent_cards").fetchall()
    existing_paths: set[str] = set()
    for row in rows:
        data = _json_loads_maybe(row["data"])
        path = str(data.get("__source_file_path") or "")
        if path and _stored_file_exists(path):
            existing_paths.add(path)
        else:
            conn.execute("DELETE FROM talent_cards WHERE id=?", (row["id"],))
            removed += 1
    conn.commit()
    conn.close()

    for file_path in _list_storage_files("talent-cards", {".pdf"}):
        if file_path in existing_paths:
            continue
        filename = Path(file_path).name
        try:
            card = _extract_talent_card_fields_from_file(file_path, filename)
        except Exception as e:
            card = {"Name": Path(filename).stem, "Status": "failed", "Original Filename": filename, "Note": f"Sync processing failed: {str(e)}", "__source_file_path": file_path, "__source_filename": filename}
        conn = get_db_connection()
        conn.execute("INSERT INTO talent_cards (data) VALUES (?)", (json.dumps(card),))
        conn.commit()
        conn.close()
        imported += 1
    if removed or imported:
        log_audit("sync", "uploads", "talent-cards", None, f"removed_records={removed}; imported_files={imported}", "success")
    return {"removed_records": removed, "imported_files": imported}


def _sync_position_profiles_with_storage() -> dict:
    return _sync_file_backed_rows("position_profiles", "position-profiles", _process_position_profiles_from_file, {".xlsx", ".xls"})


def _sync_people_model_with_storage() -> dict:
    return _sync_file_backed_rows("candidates", "people-model", _process_people_model_from_file, {".xlsx", ".xls"})


def _json_loads_maybe(value: str | None) -> dict:
    try:
        obj = json.loads(value or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _count_rows_referencing_file(table: str, file_path: str) -> int:
    conn = get_db_connection()
    rows = conn.execute(f"SELECT data FROM {table}").fetchall()
    conn.close()
    count = 0
    for row in rows:
        data = _json_loads_maybe(row["data"])
        if str(data.get("__source_file_path") or "") == str(file_path):
            count += 1
    return count


def _delete_file_if_unreferenced(table: str, file_path: str | None) -> None:
    if not file_path:
        return
    if _count_rows_referencing_file(table, file_path) == 0:
        _delete_physical_file(file_path)


def _save_upload_stream(upload: UploadFile, directory: Path, filename: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(filename)
    target = directory / safe_name
    total = 0
    with open(target, "wb") as out:
        while True:
            chunk = upload.file.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"{filename} exceeds 25 MB upload limit.")
            out.write(chunk)
    return str(target)


@router.post("/chunk/{category}")
async def upload_chunk(
    category: str,
    upload_id: str = Form(...),
    filename: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    chunk: UploadFile = File(...),
):
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid upload category.")
    safe_name = _safe_filename(filename)
    part_dir = _chunk_session_dir(upload_id)
    part_path = part_dir / f"{chunk_index:06d}.part"

    payload = await chunk.read()
    if part_path.exists():
        part_path.unlink(missing_ok=True)
    with open(part_path, "wb") as f:
        f.write(payload)

    meta = {
        "filename": safe_name,
        "total_chunks": int(total_chunks),
        "category": category,
    }
    with open(part_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)

    return {"message": f"chunk {chunk_index + 1}/{total_chunks} received"}



def _assemble_chunked_file(upload_id: str, filename: str, expected_total_chunks: int) -> str:
    session_dir = _chunk_session_dir(upload_id)
    assembled = _assembled_path(upload_id, filename)
    total = 0
    with open(assembled, "wb") as out:
        for idx in range(expected_total_chunks):
            part = session_dir / f"{idx:06d}.part"
            if not part.exists():
                raise HTTPException(status_code=400, detail=f"Missing upload chunk {idx + 1} for {filename}.")
            with open(part, "rb") as pf:
                data = pf.read()
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                assembled.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"{filename} exceeds 25 MB upload limit.")
            out.write(data)
    return str(assembled)



def _extract_pdf_text_limited(content: bytes, max_pages: int = 20, max_chars: int = 40000) -> str:
    reader = PyPDF2.PdfReader(io.BytesIO(content))
    parts = []
    for page in reader.pages[:max_pages]:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text:
            parts.append(page_text)
        joined = "\n".join(parts)
        if len(joined) >= max_chars:
            return joined[:max_chars]
    return "\n".join(parts)[:max_chars]



def _extract_pdf_text_from_path_limited(file_path: str, max_pages: int = 20, max_chars: int = 40000) -> str:
    parts = []
    local_path, cleanup = _local_readable_copy(file_path)
    if not local_path:
        return ""
    try:
        with open(local_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages[:max_pages]:
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                if page_text:
                    parts.append(page_text)
                joined = "\n".join(parts)
                if len(joined) >= max_chars:
                    return joined[:max_chars]
        return "\n".join(parts)[:max_chars]
    finally:
        _cleanup_local_copy(local_path, cleanup)



def _process_job_description_background(jd_id: int, file_path: str, filename: str) -> None:
    content = ""
    local_path, cleanup = _local_readable_copy(file_path)
    try:
        if not local_path:
            raise FileNotFoundError(f"Unable to access stored file: {file_path}")
        with open(local_path, "rb") as f:
            content_bytes = f.read()
        content = _extract_pdf_text_limited(content_bytes)
        if not content:
            content = f"Uploaded PDF: {filename}. No extractable text was found, so metadata was inferred from the filename."
        job_title = parser.extract_field(content, "Title") or parser.position_from_filename(filename)
        grade = parser.extract_field(content, "Grade") or ""
        conn = get_db_connection()
        conn.execute(
            "UPDATE job_descriptions SET job_title=?, grade=?, content=? WHERE id=?",
            (job_title, grade, content, jd_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        try:
            conn = get_db_connection()
            fallback = content or f"Background processing failed for {filename}: {str(e)}"
            conn.execute("UPDATE job_descriptions SET content=? WHERE id=?", (fallback, jd_id))
            conn.commit()
            conn.close()
        except Exception:
            pass
    finally:
        _cleanup_local_copy(local_path, cleanup)


def _extract_talent_card_fields_from_file(file_path: str, filename: str) -> dict:
    text = _extract_pdf_text_from_path_limited(file_path, max_pages=12, max_chars=30000)
    if not text:
        return {
            "Name": Path(filename).stem,
            "Status": "uploaded",
            "Original Filename": filename,
            "Note": "No extractable text found in PDF.",
            "__source_file_path": str(file_path),
            "__source_filename": _safe_filename(filename),
        }
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
        val = re.sub(r"\s*\n\s*", " ", m.group(1).strip())
        val = re.sub(r"^[•\-\u2022]+\s*", "", val)
        return val if val else "Not provided"

    def grab(regex, fallback="Not provided"):
        m = re.search(regex, text, re.IGNORECASE)
        return m.group(1).strip() if m else fallback

    return {
        "Name": grab(r"NAME\s*:?\s*(.+)", Path(filename).stem),
        "Grade": grab(r"Grade\s*:?\s*([A-Z0-9]+)"),
        "Age": grab(r"Age\s*:?\s*(\d+)"),
        "Date Joined": grab(r"Date\s*Joined\s*TNB\s*:?\s*([^\n]+)"),
        "Permanent Date": grab(r"Permanent\s*:?\s*([^\n]+)"),
        "Retirement Date": grab(r"Retirement\s*Date\s*:?\s*([^\n]+)"),
        "Strengths": extract_section("Strength", text),
        "Achievements": extract_section("Significant Achievements / Contributions", text),
        "Professional Certifications": extract_section("Professional Certifications", text),
        "Education": extract_section("Education", text),
        "Work Experience": extract_section("Work Experience", text),
        "Skills/Expertise": extract_section("Skills & Expertise", text),
        "Leadership": extract_section("Leadership Experience", text),
        "Awards": extract_section("Honours & Awards", text),
        "Career Goals": extract_section("Career Goals", text),
        "Job Preferences": extract_section("Job Preference", text),
        "Project Experience": extract_section("Project Experience", text),
        "Status": "processed",
        "Original Filename": filename,
        "__source_file_path": str(file_path),
        "__source_filename": _safe_filename(filename),
    }



def _process_talent_card_background(card_id: int, file_path: str, filename: str) -> None:
    try:
        card = _extract_talent_card_fields_from_file(file_path, filename)
    except Exception as e:
        card = {
            "Name": Path(filename).stem,
            "Status": "failed",
            "Original Filename": filename,
            "Note": f"Background processing failed: {str(e)}",
            "__source_file_path": str(file_path),
            "__source_filename": _safe_filename(filename),
        }
    conn = get_db_connection()
    conn.execute("UPDATE talent_cards SET data=? WHERE id=?", (json.dumps(card), card_id))
    conn.commit()
    conn.close()



def _process_position_profiles_from_file(file_path: str, filename: str | None = None) -> int:
    local_path, cleanup = _local_readable_copy(file_path)
    if not local_path:
        raise HTTPException(status_code=500, detail=f"Unable to access stored file: {file_path}")
    conn = get_db_connection()
    c = conn.cursor()
    try:
        df = _read_excel_from_path(local_path).where(pd.notnull, None)
        count = 0
        for _, row in df.iterrows():
            row_dict = {k: ("" if v is None else str(v)) for k, v in row.to_dict().items()}
            row_dict["__source_file_path"] = str(file_path)
            row_dict["__source_filename"] = _safe_filename(filename or Path(file_path).name)
            c.execute("INSERT INTO position_profiles (data) VALUES (?)", (json.dumps(row_dict),))
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()
        _cleanup_local_copy(local_path, cleanup)



def _process_people_model_from_file(file_path: str, filename: str | None = None) -> int:
    local_path, cleanup = _local_readable_copy(file_path)
    if not local_path:
        raise HTTPException(status_code=500, detail=f"Unable to access stored file: {file_path}")
    conn = get_db_connection()
    c = conn.cursor()
    try:
        df = _read_excel_from_path(local_path)
        required_cols = ["Name", "Job Title", "Employee ID"]
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing required columns: {', '.join(missing)}")
        df["Name"] = df["Name"].astype(str).str.strip()
        df["Job Title"] = df["Job Title"].astype(str).str.strip()
        df["Employee ID"] = df["Employee ID"].astype(str).str.strip()
        df = df[(df["Name"] != "") & (df["Job Title"] != "") & (df["Employee ID"] != "")].drop_duplicates(subset=["Employee ID"])
        count = 0
        for _, row in df.iterrows():
            row_dict = {k: str(v) for k, v in row.to_dict().items()}
            row_dict["__source_file_path"] = str(file_path)
            row_dict["__source_filename"] = _safe_filename(filename or Path(file_path).name)
            c.execute("INSERT INTO candidates (data) VALUES (?)", (json.dumps(row_dict),))
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()
        _cleanup_local_copy(local_path, cleanup)


@router.post("/finalize/{category}", status_code=202)
async def finalize_chunked_upload(
    category: str,
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
):
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid upload category.")

    uploads = payload.get("uploads") or []
    if not uploads:
        raise HTTPException(status_code=400, detail="No uploaded files to finalize.")

    errors = []
    stored = 0
    messages = []

    for item in uploads:
        upload_id = str(item.get("upload_id") or "").strip()
        filename = _safe_filename(item.get("filename") or "upload.bin")
        total_chunks = int(item.get("total_chunks") or 0)
        if not upload_id or total_chunks <= 0:
            errors.append(f"Invalid upload metadata for {filename}.")
            continue

        assembled = _assemble_chunked_file(upload_id, filename, total_chunks)

        try:
            if category == "job-descriptions":
                conn = get_db_connection()
                c = conn.cursor()
                final_path = _move_file_to_category_storage(assembled, "job-descriptions", filename)
                position = os.path.splitext(filename)[0].replace("_", " ").replace(" JD", "").strip()
                placeholder_content = f"Upload received for {filename}. Document parsing is running in background."
                inferred_title = parser.position_from_filename(filename)
                c.execute(
                    "INSERT INTO job_descriptions (position, job_title, grade, filepath, content, original_filename) VALUES (?, ?, ?, ?, ?, ?)",
                    (position, inferred_title, "", final_path, placeholder_content, filename),
                )
                jd_id = c.lastrowid
                conn.commit()
                conn.close()
                background_tasks.add_task(_process_job_description_background, jd_id, final_path, filename)
                messages.append(f"{filename} uploaded")
                stored += 1

            elif category == "talent-cards":
                conn = get_db_connection()
                c = conn.cursor()
                final_path = _move_file_to_category_storage(assembled, "talent-cards", filename)
                placeholder = {
                    "Name": Path(filename).stem,
                    "Status": "processing",
                    "Original Filename": filename,
                    "__source_file_path": final_path,
                    "__source_filename": filename,
                }
                c.execute("INSERT INTO talent_cards (data) VALUES (?)", (json.dumps(placeholder),))
                card_id = c.lastrowid
                conn.commit()
                conn.close()
                background_tasks.add_task(_process_talent_card_background, card_id, final_path, filename)
                messages.append(f"{filename} uploaded")
                stored += 1

            elif category == "position-profiles":
                stored_path = _move_file_to_category_storage(assembled, "position-profiles", filename)
                count = _process_position_profiles_from_file(stored_path, filename)
                messages.append(f"{filename}: {count} rows imported")
                stored += 1

            elif category == "people-model":
                stored_path = _move_file_to_category_storage(assembled, "people-model", filename)
                count = _process_people_model_from_file(stored_path, filename)
                messages.append(f"{filename}: {count} rows imported")
                stored += 1
        except HTTPException as e:
            errors.append(f"{filename}: {e.detail}")
        except Exception as e:
            errors.append(f"{filename}: {str(e)}")
        finally:
            _cleanup_chunk_session(upload_id)

    if stored == 0:
        raise HTTPException(status_code=400, detail="No files were uploaded successfully. " + " | ".join(errors[:3]))

    summary = f"Uploaded {stored} file(s) successfully."
    if category in {"job-descriptions", "talent-cards"}:
        summary += " Background processing is running; click Refresh after a few seconds."
    log_audit("finalize_upload", "uploads", category, None, f"stored={stored}; details={' | '.join(messages[:5])}; errors={' | '.join(errors[:5])}", "success")
    return {"message": summary, "details": messages, "errors": errors}


@router.post("/talent-cards")
async def upload_talent_cards(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...)):
    conn = get_db_connection()
    c = conn.cursor()
    stored = 0
    for f in files:
        tmp_dir = _chunk_session_dir(str(uuid.uuid4()))
        try:
            local_path = _save_upload_stream(f, tmp_dir, f.filename)
            file_path = _move_file_to_category_storage(local_path, "talent-cards", f.filename)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        placeholder = {
            "Name": Path(_safe_filename(f.filename)).stem,
            "Status": "processing",
            "Original Filename": _safe_filename(f.filename),
            "__source_file_path": file_path,
            "__source_filename": _safe_filename(f.filename),
        }
        c.execute("INSERT INTO talent_cards (data) VALUES (?)", (json.dumps(placeholder),))
        card_id = c.lastrowid
        background_tasks.add_task(_process_talent_card_background, card_id, file_path, f.filename)
        stored += 1
    conn.commit()
    conn.close()
    log_audit("upload", "uploads", "talent-cards", None, f"uploaded={stored}", "success")
    return {"message": f"Successfully uploaded {stored} talent card(s)."}


@router.get("/talent-cards")
def get_talent_cards():
    _sync_talent_cards_with_storage()
    conn = get_db_connection()
    rows = conn.execute("SELECT id, data FROM talent_cards ORDER BY id DESC").fetchall()
    conn.close()
    results = []
    for row in rows:
        data = json.loads(row["data"])
        data["DB_ID"] = row["id"]
        results.append(data)
    return results


@router.delete("/talent-cards/{card_id}")
def delete_talent_card(card_id: int):
    conn = get_db_connection()
    row = conn.execute("SELECT data FROM talent_cards WHERE id=?", (card_id,)).fetchone()
    if row:
        data = _json_loads_maybe(row["data"])
        _delete_physical_file(data.get("__source_file_path"))
    conn.execute("DELETE FROM talent_cards WHERE id=?", (card_id,))
    conn.commit()
    conn.close()
    log_audit("delete", "uploads", "talent-cards", str(card_id), f"Deleted Talent Card {card_id}", "success")
    return {"message": f"Deleted Talent Card {card_id}"}


@router.delete("/talent-cards")
def delete_all_talent_cards():
    conn = get_db_connection()
    rows = conn.execute("SELECT data FROM talent_cards").fetchall()
    for row in rows:
        data = _json_loads_maybe(row["data"])
        _delete_physical_file(data.get("__source_file_path"))
    conn.execute("DELETE FROM talent_cards")
    conn.commit()
    conn.close()
    log_audit("delete_all", "uploads", "talent-cards", None, "All Talent Cards deleted", "success")
    return {"message": "All Talent Cards deleted"}


@router.post("/position-profiles")
async def upload_position_profiles(file: UploadFile = File(...)):
    tmp_dir = _chunk_session_dir(str(uuid.uuid4()))
    try:
        local_path = _save_upload_stream(file, tmp_dir, file.filename)
        stored_path = _move_file_to_category_storage(local_path, "position-profiles", file.filename)
        count = _process_position_profiles_from_file(stored_path, file.filename)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    log_audit("upload", "uploads", "position-profiles", None, f"rows_imported={count}", "success")
    return {"message": f"Position profiles uploaded successfully ({count} rows)"}


@router.get("/position-profiles")
def get_position_profiles():
    _sync_position_profiles_with_storage()
    conn = get_db_connection()
    rows = conn.execute("SELECT id, data FROM position_profiles ORDER BY id DESC").fetchall()
    conn.close()
    results = []
    for row in rows:
        data = json.loads(row["data"])
        data["DB_ID"] = row["id"]
        results.append(data)
    return results


@router.delete("/position-profiles/{profile_id}")
def delete_position_profile(profile_id: int):
    conn = get_db_connection()
    row = conn.execute("SELECT data FROM position_profiles WHERE id=?", (profile_id,)).fetchone()
    file_path = None
    if row:
        file_path = _json_loads_maybe(row["data"]).get("__source_file_path")
    conn.execute("DELETE FROM position_profiles WHERE id=?", (profile_id,))
    conn.commit()
    conn.close()
    _delete_file_if_unreferenced("position_profiles", file_path)
    log_audit("delete", "uploads", "position-profiles", str(profile_id), f"Deleted Position Profile {profile_id}", "success")
    return {"message": f"Deleted Position Profile {profile_id}"}


@router.delete("/position-profiles")
def delete_all_position_profiles():
    conn = get_db_connection()
    rows = conn.execute("SELECT data FROM position_profiles").fetchall()
    file_paths = {_json_loads_maybe(row["data"]).get("__source_file_path") for row in rows}
    conn.execute("DELETE FROM position_profiles")
    conn.commit()
    conn.close()
    for file_path in file_paths:
        _delete_physical_file(file_path)
    log_audit("delete_all", "uploads", "position-profiles", None, "All Position Profiles deleted", "success")
    return {"message": "All Position Profiles deleted"}


@router.post("/job-descriptions", status_code=202)
async def upload_job_descriptions(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...)):
    conn = get_db_connection()
    c = conn.cursor()
    stored = 0
    for file in files:
        filename = _safe_filename(file.filename or "document.pdf")
        position = os.path.splitext(filename)[0].replace("_", " ").replace(" JD", "").strip()
        tmp_dir = _chunk_session_dir(str(uuid.uuid4()))
        try:
            local_path = _save_upload_stream(file, tmp_dir, filename)
            save_path = _move_file_to_category_storage(local_path, "job-descriptions", filename)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        placeholder_content = f"Upload received for {filename}. Document parsing is running in background."
        inferred_title = parser.position_from_filename(filename)
        c.execute(
            "INSERT INTO job_descriptions (position, job_title, grade, filepath, content, original_filename) VALUES (?, ?, ?, ?, ?, ?)",
            (position, inferred_title, "", save_path, placeholder_content, filename),
        )
        jd_id = c.lastrowid
        background_tasks.add_task(_process_job_description_background, jd_id, save_path, filename)
        stored += 1
    conn.commit()
    conn.close()
    log_audit("upload", "uploads", "job-descriptions", None, f"uploaded={stored}", "success")
    return {"message": f"Uploaded {stored} Job Description file(s). Parsing will continue in background."}


@router.get("/job-descriptions")
def get_job_descriptions():
    _sync_job_descriptions_with_storage()
    df = fetch_dataframe(
        "SELECT id, position, job_title, grade, filepath, COALESCE(original_filename, '') AS original_filename FROM job_descriptions ORDER BY id DESC"
    )
    records = df.to_dict(orient="records")
    return records




@router.get("/job-descriptions/{jd_id}/content")
def get_job_description_content(jd_id: int):
    _sync_job_descriptions_with_storage()
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT id, position, job_title, grade, filepath, content, COALESCE(original_filename, '') AS original_filename
        FROM job_descriptions
        WHERE id=?
        """,
        (jd_id,),
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Selected JD not found")

    record = dict(row)
    content = record.get("content") or ""
    placeholder_markers = [
        "Document parsing is running in background",
        "No extractable text was found",
        "Background processing failed",
    ]
    if record.get("filepath") and (not content.strip() or any(marker in content for marker in placeholder_markers)):
        extracted = _extract_pdf_text_from_path_limited(record.get("filepath") or "", max_pages=50, max_chars=120000)
        if extracted.strip():
            content = extracted
            try:
                conn = get_db_connection()
                conn.execute("UPDATE job_descriptions SET content=? WHERE id=?", (content, jd_id))
                conn.commit()
                conn.close()
            except Exception:
                pass

    return {
        "id": record.get("id"),
        "position": record.get("position") or "",
        "job_title": record.get("job_title") or record.get("position") or record.get("original_filename") or "Untitled JD",
        "grade": record.get("grade") or "",
        "original_filename": record.get("original_filename") or Path(record.get("filepath") or "").name,
        "filepath": record.get("filepath") or "",
        "content": content,
    }

@router.delete("/job-descriptions/{jd_id}")
def delete_job_description(jd_id: int):
    conn = get_db_connection()
    row = conn.execute("SELECT filepath FROM job_descriptions WHERE id=?", (jd_id,)).fetchone()
    if row:
        _delete_physical_file(row["filepath"])
    conn.execute("DELETE FROM job_descriptions WHERE id=?", (jd_id,))
    conn.commit()
    conn.close()
    log_audit("delete", "uploads", "job-descriptions", str(jd_id), f"Deleted JD {jd_id}", "success")
    return {"message": f"Deleted JD {jd_id}"}


@router.delete("/job-descriptions")
def delete_all_job_descriptions():
    conn = get_db_connection()
    rows = conn.execute("SELECT filepath FROM job_descriptions").fetchall()
    for row in rows:
        _delete_physical_file(row["filepath"])
    conn.execute("DELETE FROM job_descriptions")
    conn.commit()
    conn.close()
    log_audit("delete_all", "uploads", "job-descriptions", None, "All JDs deleted", "success")
    return {"message": "All JDs deleted"}


@router.post("/people-model")
async def upload_people_model(file: UploadFile = File(...)):
    tmp_dir = _chunk_session_dir(str(uuid.uuid4()))
    try:
        local_path = _save_upload_stream(file, tmp_dir, file.filename)
        stored_path = _move_file_to_category_storage(local_path, "people-model", file.filename)
        count = _process_people_model_from_file(stored_path, file.filename)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    log_audit("upload", "uploads", "people-model", None, f"rows_imported={count}", "success")
    return {"message": f"People model uploaded successfully ({count} rows)"}


@router.get("/people-model")
def get_people_model():
    _sync_people_model_with_storage()
    conn = get_db_connection()
    rows = conn.execute("SELECT id, data FROM candidates ORDER BY id DESC").fetchall()
    conn.close()
    results = []
    for row in rows:
        data = json.loads(row["data"])
        data["DB_ID"] = row["id"]
        results.append(data)
    return results


@router.delete("/people-model/{candidate_id}")
def delete_people_model(candidate_id: int):
    conn = get_db_connection()
    row = conn.execute("SELECT data FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    file_path = None
    if row:
        file_path = _json_loads_maybe(row["data"]).get("__source_file_path")
    conn.execute("DELETE FROM candidates WHERE id=?", (candidate_id,))
    conn.commit()
    conn.close()
    _delete_file_if_unreferenced("candidates", file_path)
    log_audit("delete", "uploads", "people-model", str(candidate_id), f"Deleted Candidate {candidate_id}", "success")
    return {"message": f"Deleted Candidate {candidate_id}"}


@router.delete("/people-model")
def delete_all_people_model():
    conn = get_db_connection()
    rows = conn.execute("SELECT data FROM candidates").fetchall()
    file_paths = {_json_loads_maybe(row["data"]).get("__source_file_path") for row in rows}
    conn.execute("DELETE FROM candidates")
    conn.commit()
    conn.close()
    for file_path in file_paths:
        _delete_physical_file(file_path)
    log_audit("delete_all", "uploads", "people-model", None, "All Candidates deleted", "success")
    return {"message": "All Candidates deleted"}
