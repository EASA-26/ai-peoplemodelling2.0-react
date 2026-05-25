# PostgreSQL primary + vector bootstrap

This folder adds PostgreSQL-ready schema for the AI People Modelling app without changing existing routes, UI, or configuration keys.

## Existing configuration remains the same
Use either:
- `DATABASE_URL`
- or `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`

## What gets created automatically
When the app starts against PostgreSQL, `backend/app/data/db.py` now creates:
- primary tables used by the current app
- vector-ready tables for future embedding storage
- `pgvector` extension if allowed

## Manual setup
Run in order:
1. `01_primary_schema.sql`
2. `02_vector_schema.sql`

If pgvector cannot be enabled, use:
1. `01_primary_schema.sql`
2. `03_vector_fallback_schema.sql`
