import os
import sqlite3
import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel
from ...data.db import log_audit

router = APIRouter()
DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "hr_ai.db")

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/login")
def login(req: LoginRequest):
    if req.username == "admin" and req.password == "genco2025":
        log_audit("login", "auth", "session", req.username, "User login successful", "success", req.username)
        return {"logged_in": True, "user": req.username, "token": "dummy-token-123"}
    log_audit("login", "auth", "session", req.username, "Invalid username or password", "failed", req.username)
    return {"logged_in": False, "error": "Invalid username or password"}
