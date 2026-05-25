from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .app.api.routers import analytics, audit, auth, recommendations, uploads
from .app.data.db import get_db_path, get_upload_root, init_db

app = FastAPI(title="AI Powered HR People Modelling", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.on_event("startup")
def startup_event():
    init_db()


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(audit.router, prefix="/api/audit-logs", tags=["audit"])
app.include_router(uploads.router, prefix="/api/uploads", tags=["uploads"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(recommendations.router, prefix="/api/recommendations", tags=["recommendations"])

frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
assets_dir = frontend_dist / "assets"

if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/api/health")
def health():
    return {"status": "ok", "message": "Backend is running"}


@app.get("/api/health/storage")
def storage_health():
    return {
        "status": "ok",
        "database_path": get_db_path(),
        "upload_root": get_upload_root(),
        "frontend_found": frontend_dist.exists(),
    }


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    index_file = frontend_dist / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"status": "ok", "message": "Frontend build not found. Build frontend/dist before deployment."}
