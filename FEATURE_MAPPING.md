# Feature Mapping: Streamlit to React + FastAPI

| Original Streamlit Page/Function | New React Component / Layout | Backend API Route | Status |
| :--- | :--- | :--- | :--- |
| `_login_gate` | `LoginPage.tsx` | `/api/auth/login` | ✅ Migrated |
| `analytics_page` | `DashboardPage.tsx` | `/api/analytics/summary` | ✅ Migrated |
| `succession_recommendation` | `SuccessionPage.tsx` | `/api/recommendations/succession` | ✅ Migrated |
| `person_to_position_page` | `PersonToPositionPage.tsx` | `/api/recommendations/person-to-position` | ✅ Migrated |
| `upload_job_descriptions` | `DataManagementPage.tsx` (JDs Tab) | `/api/uploads/job-descriptions` | ✅ Migrated |
| `upload_people_model` | `DataManagementPage.tsx` (People Tab) | `/api/uploads/people-model` | ✅ Migrated |
| `upload_position_profiles` | `DataManagementPage.tsx` (Profiles Tab) | `/api/uploads/position-profiles` | ✅ Migrated |
| `upload_talent_cards` | `DataManagementPage.tsx` (Talent Tab) | `/api/uploads/talent-cards` | ✅ Migrated |

### Concept Preservation
- All **Education Bucket Mapping**, **Grade Logic**, **Similarity Scoring**, and **JD Disciplines** were explicitly rewritten and preserved in `backend/app/services/scoring.py` and `backend/app/services/parser.py`.
- **Databricks Inferencing** was ported via `backend/app/services/llm.py`, maintaining its fallback mechanism between standard `get_open_ai_client`, OpenAI REST, and generic Invocation payloads.
