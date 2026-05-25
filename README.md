# AI Powered HR People Modelling (Migrated)

This repository contains the migrated production-ready React + FastAPI architecture for the HR Succession Dashboard.

## Architecture Highlights
*   **React Frontend**: Scalable single-page application built with React, Vite, Tailwind CSS, and shadcn styling.
*   **FastAPI Backend**: Fully decoupled Python layer serving robust HTTP APIs to handle storage and inference.
*   **Permanent Storage**: Uses SQLite and persistent storage folders (`backend/app/data/uploads`) rather than ephemeral in-memory Streamlit data structures.

## Installation and Running

### 1. Start the Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

*The backend includes Databricks Llama-4 inferencing using workspace tokens (inherited from `os.environ` if run within a valid context).*

### 2. Start the Frontend
```bash
cd frontend
npm install
npm run dev
```

*The frontend opens typically at `http://localhost:5173/`. Login with `admin` / `genco2025`.*


## PostgreSQL primary + vector support

The app now supports PostgreSQL as the primary database using the existing configuration keys:
- `DATABASE_URL`
- or `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`

When PostgreSQL is used, startup automatically creates:
- current application tables
- audit log table
- vector-ready tables for future embeddings

Reference SQL files are in `database/postgresql/`.
