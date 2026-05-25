import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import create_engine, text

DEFAULT_UC_VOLUME_ROOT = "/Volumes/genco/hr_ai/hr_people_app"


def _can_use_local_path(path: Path, create: bool = False) -> bool:
    try:
        if path.exists():
            return path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        parent = path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not parent.exists() or not os.access(parent, os.W_OK | os.X_OK):
            return False
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def _runtime_storage_root() -> Path:
    # Prefer a writable app-local directory so Databricks App startup never fails
    # when /Volumes is not mounted or not writable as a local filesystem path.
    candidates = []

    configured = os.getenv("HR_PEOPLE_APP_STORAGE_ROOT", "").strip()
    if configured:
        candidates.append(Path(configured))

    uc_env = (os.getenv("HR_PEOPLE_APP_UC_VOLUME") or os.getenv("VOLUME") or "").strip()
    if uc_env:
        candidates.append(Path(uc_env) / ".app_state")

    cwd_root = Path.cwd() / ".app_state"
    module_root = Path(__file__).resolve().parents[3] / ".app_state"
    tmp_root = Path("/tmp/hr_people_app/.app_state")
    candidates.extend([cwd_root, module_root, tmp_root])

    for candidate in candidates:
        if _can_use_local_path(candidate, create=True):
            return candidate

    # Final fallback: return cwd path even if creation is deferred; downstream code
    # may still succeed in environments with late-mounted storage.
    return cwd_root


def _default_storage_root() -> Path:
    return _runtime_storage_root()


APP_STORAGE_ROOT = _default_storage_root()
UPLOADS_ROOT = APP_STORAGE_ROOT / "uploads"
DB_FILE = APP_STORAGE_ROOT / "hr_ai.db"
UC_VOLUME_CATEGORY_DIRS = {
    "job-descriptions": "JD",
    "position-profiles": "Position Profile",
    "talent-cards": "Talent Cards",
    "people-model": "Talent Profile",
}

_engine = None


def ensure_storage_dirs() -> None:
    global APP_STORAGE_ROOT, UPLOADS_ROOT, DB_FILE
    if not _can_use_local_path(APP_STORAGE_ROOT, create=True):
        APP_STORAGE_ROOT = _runtime_storage_root()
        UPLOADS_ROOT = APP_STORAGE_ROOT / "uploads"
        DB_FILE = APP_STORAGE_ROOT / "hr_ai.db"
    APP_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    ensure_uc_volume_dirs()


def get_db_path() -> str:
    ensure_storage_dirs()
    return str(DB_FILE)


def get_upload_root() -> str:
    ensure_storage_dirs()
    return str(UPLOADS_ROOT)




def get_uc_volume_root() -> str:
    return str(os.getenv("HR_PEOPLE_APP_UC_VOLUME") or os.getenv("VOLUME") or DEFAULT_UC_VOLUME_ROOT)


def is_uc_volume_configured() -> bool:
    return get_uc_volume_root().startswith("/Volumes/")


def get_uc_volume_category_dir(category: str) -> str:
    name = UC_VOLUME_CATEGORY_DIRS.get(category)
    if not name:
        raise ValueError(f"Unknown upload category: {category}")
    return str(Path(get_uc_volume_root()) / name)


def ensure_uc_volume_dirs() -> None:
    for name in UC_VOLUME_CATEGORY_DIRS.values():
        target = Path(get_uc_volume_root()) / name
        try:
            os.makedirs(target, exist_ok=True)
        except Exception:
            # Keep local/dev compatibility, but do not interfere with explicit write attempts.
            pass

def get_database_url() -> str:
    direct = os.getenv("DATABASE_URL", "").strip()
    if direct:
        if direct.startswith("postgresql://") and "+pg8000" not in direct:
            return direct.replace("postgresql://", "postgresql+pg8000://", 1)
        return direct
    host = os.getenv("POSTGRES_HOST", "").strip()
    if host:
        port = os.getenv("POSTGRES_PORT", "5432").strip()
        db = os.getenv("POSTGRES_DB", "postgres").strip()
        user = os.getenv("POSTGRES_USER", "postgres").strip()
        password = os.getenv("POSTGRES_PASSWORD", "").strip()
        return f"postgresql+pg8000://{user}:{password}@{host}:{port}/{db}"
    return f"sqlite:///{get_db_path()}"


def is_postgres_url(url: Optional[str] = None) -> bool:
    target = (url or get_database_url()).lower()
    return target.startswith("postgresql")


def _get_engine():
    global _engine
    if _engine is None:
        url = get_database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    return _engine


class CompatRow(dict):
    def __getattr__(self, item):
        return self[item]


class CompatCursor:
    def __init__(self, conn: "CompatConnection"):
        self.conn = conn
        self._rows = []
        self.lastrowid = None
        self.description = None

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None):
        sql_named, param_map = self.conn._normalize(sql, params)
        result = self.conn._sa_conn.execute(text(sql_named), param_map)
        self.lastrowid = getattr(result, "lastrowid", None)
        if result.returns_rows:
            self._rows = [CompatRow(dict(r._mapping)) for r in result.fetchall()]
            keys = list(self._rows[0].keys()) if self._rows else []
            self.description = [(k, None, None, None, None, None, None) for k in keys]
        else:
            self._rows = []
            self.description = []
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class CompatConnection:
    def __init__(self):
        self._sa_conn = _get_engine().connect()
        self.row_factory = sqlite3.Row

    def _normalize(self, sql: str, params: Optional[Iterable[Any]]):
        if params is None:
            return sql, {}
        if isinstance(params, dict):
            return sql, params
        parts = sql.split("?")
        if len(parts) == 1:
            return sql, {}
        names = []
        rebuilt = []
        for i, part in enumerate(parts[:-1]):
            name = f"p{i}"
            names.append(name)
            rebuilt.append(part + f":{name}")
        rebuilt.append(parts[-1])
        return "".join(rebuilt), {name: value for name, value in zip(names, params)}

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None):
        return CompatCursor(self).execute(sql, params)

    def cursor(self):
        return CompatCursor(self)

    def commit(self):
        self._sa_conn.commit()

    def close(self):
        self._sa_conn.close()


def _create_primary_tables(conn, is_sqlite: bool) -> None:
    auto_pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "BIGSERIAL PRIMARY KEY"
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS job_descriptions (
            id {auto_pk},
            position TEXT,
            job_title TEXT,
            grade TEXT,
            filepath TEXT,
            content TEXT,
            original_filename TEXT
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_job_descriptions_filepath ON job_descriptions (filepath)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_job_descriptions_job_title ON job_descriptions (job_title)"))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS candidates (
            id {auto_pk},
            data TEXT
        )
    """))
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS position_profiles (
            id {auto_pk},
            data TEXT
        )
    """))
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS talent_cards (
            id {auto_pk},
            data TEXT
        )
    """))
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id {auto_pk},
            username TEXT,
            action TEXT,
            module TEXT,
            entity_type TEXT,
            entity_id TEXT,
            details TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs (created_at)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_audit_logs_module_action ON audit_logs (module, action)"))

    try:
        conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN original_filename TEXT"))
    except Exception:
        pass


def _create_postgres_vector_tables(conn) -> None:
    # Best-effort pgvector enablement. Existing app functions do not depend on these tables.
    try:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception:
        # Allow deployment on PostgreSQL even when extension creation is restricted.
        pass

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS vector_collections (
            id BIGSERIAL PRIMARY KEY,
            collection_name TEXT NOT NULL UNIQUE,
            embedding_dimensions INTEGER NOT NULL DEFAULT 1536,
            distance_metric TEXT NOT NULL DEFAULT 'cosine',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS source_documents (
            id BIGSERIAL PRIMARY KEY,
            doc_type TEXT NOT NULL,
            source_table TEXT,
            source_id BIGINT,
            title TEXT,
            filepath TEXT,
            content TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_source_documents_doc_type ON source_documents (doc_type)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_source_documents_source_ref ON source_documents (source_table, source_id)"))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id BIGSERIAL PRIMARY KEY,
            document_id BIGINT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            token_count INTEGER,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (document_id, chunk_index)
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks (document_id)"))

    vector_ready = True
    try:
        conn.execute(text("SELECT '[0,0,0]'::vector"))
    except Exception:
        vector_ready = False

    if vector_ready:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS document_embeddings (
                id BIGSERIAL PRIMARY KEY,
                collection_id BIGINT NOT NULL REFERENCES vector_collections(id) ON DELETE CASCADE,
                chunk_id BIGINT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
                embedding vector(1536) NOT NULL,
                embedding_model TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (chunk_id)
            )
        """))
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_document_embeddings_hnsw ON document_embeddings USING hnsw (embedding vector_cosine_ops)"
            ))
        except Exception:
            try:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_document_embeddings_ivfflat ON document_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
                ))
            except Exception:
                pass
    else:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS document_embeddings (
                id BIGSERIAL PRIMARY KEY,
                collection_id BIGINT NOT NULL REFERENCES vector_collections(id) ON DELETE CASCADE,
                chunk_id BIGINT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
                embedding_json TEXT NOT NULL,
                embedding_model TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (chunk_id)
            )
        """))

    conn.execute(text("""
        INSERT INTO vector_collections (collection_name, embedding_dimensions, distance_metric)
        VALUES
            ('job_descriptions', 1536, 'cosine'),
            ('candidates', 1536, 'cosine'),
            ('position_profiles', 1536, 'cosine'),
            ('talent_cards', 1536, 'cosine')
        ON CONFLICT (collection_name) DO NOTHING
    """))


def init_db():
    engine = _get_engine()
    url = get_database_url()
    is_sqlite = url.startswith("sqlite")
    with engine.begin() as conn:
        _create_primary_tables(conn, is_sqlite=is_sqlite)
        if is_postgres_url(url):
            _create_postgres_vector_tables(conn)


def get_db_connection():
    ensure_storage_dirs()
    return CompatConnection()


def fetch_rows(sql: str, params: Optional[Iterable[Any]] = None):
    conn = get_db_connection()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def fetch_dataframe(sql: str, params: Optional[Iterable[Any]] = None):
    import pandas as pd
    rows = fetch_rows(sql, params)
    return pd.DataFrame([dict(r) for r in rows])


def log_audit(action: str, module: str, entity_type: str, entity_id: Optional[str] = None, details: Optional[str] = None, status: str = "success", username: str = "admin") -> None:
    conn = None
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO audit_logs (username, action, module, entity_type, entity_id, details, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username or "admin", action, module, entity_type, entity_id, details or "", status or "success"),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def fetch_audit_logs(limit: int = 200):
    return fetch_rows(
        "SELECT id, username, action, module, entity_type, entity_id, details, status, created_at FROM audit_logs ORDER BY id DESC LIMIT ?",
        (limit,),
    )
