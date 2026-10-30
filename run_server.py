"""Entry point for the Windows/IIS deployment on gencodev.tnb.com.my.

Task Scheduler runs this as SYSTEM through run-peoplemodelling.cmd. It exists because
a scheduled task gets none of the environment a developer's shell has: the settings the
app needs (DATABRICKS_TOKEN, PORT, storage root) live in .env.local, and nothing else
would ever read that file.

Not used on Databricks Apps -- app.yaml runs uvicorn directly there, with the workspace
supplying identity and port.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
ENV_FILE = REPO / ".env.local"

# The app binds loopback ONLY. IIS is the single thing that reaches it, over
# 127.0.0.1, and the box is shared with about twenty other teams -- an app listening on
# a routable address here is an app anyone on the internal network can reach, with this
# one having no authentication of its own yet.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8021


def load_env_file(path: Path) -> int:
    """Read KEY=value lines into os.environ. Returns how many were set.

    Deliberately strict about the format and silent about the values: a token must
    never reach a log. A real environment variable already set wins, so a one-off
    override does not need the file edited.
    """
    if not path.exists():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        # No quote stripping: .env.local is documented as unquoted, and a stripped
        # quote silently changes a password that legitimately contains one.
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def main() -> int:
    loaded = load_env_file(ENV_FILE)
    # Names only -- never values.
    print(f"people-modelling: loaded {loaded} setting(s) from {ENV_FILE.name}", flush=True)
    if not ENV_FILE.exists():
        print(
            f"people-modelling: WARNING {ENV_FILE} not found. "
            "Notepad's Save As writes .env.local.txt; check with: dir .env*",
            flush=True,
        )

    host = os.environ.get("HOST", DEFAULT_HOST)
    try:
        port = int(os.environ.get("PORT", DEFAULT_PORT))
    except ValueError:
        print(f"people-modelling: PORT is not a number; using {DEFAULT_PORT}", flush=True)
        port = DEFAULT_PORT

    if not os.environ.get("DATABRICKS_TOKEN"):
        # Not fatal: ingestion, the rules-based shortlist, analytics and audit all work
        # without it. Only the AI readiness column needs it.
        print(
            "people-modelling: no DATABRICKS_TOKEN set -- AI recommendations will fail, "
            "everything else works",
            flush=True,
        )

    sys.path.insert(0, str(REPO))
    import uvicorn

    print(f"people-modelling: start: binding {host}:{port}", flush=True)
    uvicorn.run("backend.main:app", host=host, port=port, log_level="info", access_log=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
