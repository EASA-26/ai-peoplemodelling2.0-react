from fastapi import APIRouter, Query

from ...data.db import fetch_audit_logs

router = APIRouter()


@router.get("")
def get_audit_logs(limit: int = Query(default=200, ge=1, le=1000)):
    rows = fetch_audit_logs(limit=limit)
    return [dict(r) for r in rows]
