# Migration Notes

### Key Assumptions
1. **Frontend Proxy**: The frontend `axios` calls are hardcoded to `http://localhost:8000/`. In a real Databricks Apps setup, environment variables or relative pathing (if served together) should be used.
2. **Databricks SDK**: We assume the environment running `FastAPI` has the appropriate Databricks IAM permissions to hit `databricks-llama-4-maverick` without explicit runtime DB API keys (which is standard behavior for Databricks Apps).

### Storage and Database Changes
1. **Permanent Persistence**: The `SQLite` database `hr_ai.db` is now generated in `backend/app/data/hr_ai.db`. This folder mimics a permanent disk space, isolated from UI reloads.
2. **File Storage**: The uploaded PDF and Excel artifacts are stored securely inside `backend/app/data/uploads/*` keeping their data persistent across restarts. Streamlit's implicit caching patterns were removed and converted to explicit REST file persistence logic.
3. The schema uses identical tables (`job_descriptions`, `candidates`, `position_profiles`, `talent_cards`). Data uploaded via the previous Streamlit local file setup can run through this DB if copied into the new folder structure.
