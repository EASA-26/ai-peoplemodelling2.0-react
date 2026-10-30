# People Modelling.AI — Digital Value Framework

> **Executive summary.** People Modelling.AI turns TNB GENCO's succession planning from a manual, multi-day document review into a repeatable, auditable pipeline — codifying TNB's own grade-band and discipline rules in software, and running entirely inside TNB's Databricks workspace so talent data never leaves the tenant.

*Prepared for TNB GENCO · Currency: Malaysian Ringgit (RM) · Codebase measured 12 September 2026 at commit `6b04d18`*

---

## 1. Objective

| Level | Objective |
| :--- | :--- |
| **Primary** | Recommend and rank internal succession candidates for a given position, with AI-predicted readiness and written justification |
| **Strategic** | Replace spreadsheet-and-PDF talent review with a standardised, defensible process that retains TNB's succession rules as institutional software rather than individual expertise |
| **Operational** | Centralise job descriptions, talent cards, position profiles and the people model in one store; match in both directions (position to people, person to positions); log every decision for audit |

---

## 2. Function

**Measured scope** (excludes the superseded Streamlit prototype and all build output): **2,293 lines** of backend Python across 16 files · **1,637 lines** of frontend TypeScript/React across 5 pages · **25 REST API endpoints** across 5 routers · **128 lines** of PostgreSQL schema · 5 functional modules.

| Module | What it does |
| :--- | :--- |
| **Succession Recommendation** | Position to ranked candidates. Auto-detects the best-matching job description by title, grade, project and specialty token scoring; filters the talent pool by grade band and education discipline; computes a succession score; batches the shortlist to the LLM for readiness classification and reasoning |
| **Person-to-Position** | The inverse direction — given an employee, returns the top-K positions they qualify for, with the same rule filtering applied |
| **Data Management** | Chunked upload and background processing of PDF and Excel sources across four categories (job descriptions, talent cards, position profiles, people model); reconciles database rows against physical storage on every read |
| **Analytics Dashboard** | Live counts of positions by project, positions by grade, and employees by grade |
| **Audit Log** | Every login, upload, deletion and recommendation run written to an indexed `audit_logs` table with actor, module, entity and outcome |

### The differentiating logic is the domain rules, not the model

The LLM never sees the full talent pool. It ranks a shortlist that deterministic, TNB-specific rules have already produced — which is what makes the output defensible in a talent review:

- **Grade-band adjacency.** A hand-built map of which grades are legitimate feeders for which (`M16`, `M15`, `E16&E17`, `E14&E15`, `E12&E13` and their compact variants), including cross-family tokens `CM`/`GM`/`M`/`E`. This encodes TNB's promotion structure; no external product knows it.
- **Education discipline bucketing.** Free-text qualifications are resolved into engineering families (civil, electrical, mechanical, electronic/C&I, plus the general engineering family) and the law and finance families — covering roughly 40 discipline keywords and their abbreviations.
- **Narrow versus broad JD requirements.** A job description asking for "civil engineering" is distinguished from one asking for "engineering", so a broad requirement does not silently admit every discipline and a narrow one is not over-matched.
- **TNB talent-card parsing.** Eleven named sections are extracted from the standard talent-card PDF layout (strengths, achievements, certifications, education, work experience, skills, leadership, awards, career goals, job preference, project experience), plus grade, age, join date and retirement date.
- **Project code normalisation.** NHEP, HHFS, HESS and NENGGIRI filename prefixes are resolved to canonical project names.
- **Succession scoring.** A transparent points model over KPI history, critical-role flag and years of experience — auditable, unlike a model-generated score.

### Architecture

React 19 + Vite + Tailwind single-page frontend, served by a FastAPI backend that also exposes the API. Storage is SQLite by default and PostgreSQL when configured, behind a compatibility layer, with vector-ready tables (pgvector, plus a fallback schema) already provisioned for future semantic retrieval. Inference runs against a Databricks serving endpoint (`databricks-llama-4-maverick`) using workspace identity — no third-party API keys — with a three-tier call fallback and exponential-backoff retry. Deployment targets Databricks Apps with Unity Catalog Volume storage.

---

## 3. Effort Taken (Development Mandays)

> **Estimate to validate.** The workstream figures below are reconstructed from measured code volume, module count and the scope of domain-rule elicitation. They are not timesheet data. Confirm against the actual project record before external use.

| Workstream | Mandays |
| :--- | ---: |
| **Discovery and HR rule elicitation** (grade bands, discipline families, talent-card layout) | 12 |
| **Document parsing and ingestion pipeline** (PDF/Excel, chunked upload, background processing) | 22 |
| **Matching and scoring engine** (education buckets, grade adjacency, JD auto-detection, succession score) | 25 |
| **LLM integration** (Databricks endpoint, fallback chain, prompt design, response parsing) | 12 |
| **Backend API** (25 endpoints, storage reconciliation, audit logging) | 20 |
| **React frontend** (5 pages, Excel/PDF export) | 25 |
| **Database and persistence** (SQLite/PostgreSQL layer, schema, vector-ready tables) | 10 |
| **Databricks Apps deployment and UC Volume integration** | 8 |
| **Migration from Streamlit prototype** (parity verification across 8 pages) | 10 |
| **Testing, UAT and documentation** | 14 |
| **Total** | **158** |

At a team of 2–3, this represents roughly **3–4 months** of elapsed delivery. At an assumed blended day rate of **RM 800–1,500** — *an assumption to confirm with procurement, not a quoted rate* — the equivalent external build cost is **RM 126,000–237,000**.

---

## 4. Market Solutions & Subscription Cost

**There is no direct off-the-shelf equivalent.** Every category below solves part of the problem, but none encodes TNB's grade bands, discipline rules or talent-card format — that configuration work would be required on top of any licence fee. That absence is itself a value point.

| Category (example products) | Indicative cost | Gap vs People Modelling.AI |
| :--- | :--- | :--- |
| **Enterprise HCM succession modules** (SAP SuccessFactors, Oracle HCM, Workday) | RM 200–600 per employee per year | Generic 9-box and competency models; TNB grade-band and discipline logic must be custom-configured; vendor-cloud hosted, so talent data leaves the tenant |
| **Standalone talent review and succession tools** (Cornerstone, PeopleFluent) | RM 120–350 per employee per year | Expect clean structured HRIS records; cannot ingest the existing TNB talent-card and JD PDFs without a separate data programme |
| **AI candidate-matching platforms** (Eightfold-class, HiredScore-class) | RM 250–700 per employee per year | Oriented to external recruitment and CV matching; no internal grade-progression rules and no readiness horizon for existing staff |
| **General-purpose LLM subscriptions** (per-seat chat assistants) | RM 90–150 per seat per month | No persistence, no deterministic shortlist, no audit trail; every review is an unrepeatable one-off conversation |

*All figures are indicative list-price ranges for scoping only — **verify in procurement** before use in a business case.*

---

## 5. Productivity Improvement vs. manual process

> **Estimate to validate.** The "before" column reflects the manual process as described; the improvement percentages follow from it. Confirm the baseline with the HR team before external use.

| Task | Before → After | Est. improvement |
| :--- | :--- | :--- |
| **Shortlist candidates for one position** | Manual read of the talent pool with grade and education cross-checked by hand, 2–3 days → rules engine returns a ranked, filtered shortlist in seconds | 90–95% |
| **Extract structured fields from a talent card** | Manual transcription into a spreadsheet, 15–20 minutes per card → parsed automatically on upload | 95%+ |
| **Match one person to open positions** | Compare the individual against each JD in turn, around half a day → top-K positions returned in under a minute | 85–95% |
| **Draft readiness justification** | Written from scratch per candidate, 30–60 minutes → AI-drafted reasoning reviewed and edited by HR | 60–75% |
| **Workforce analytics by project and grade** | Rebuild pivot tables in Excel per request, 2–4 hours → dashboard reads live from the database | 80–90% |
| **Evidence trail for a talent decision** | Reconstructed from email and file versions after the fact → every run logged automatically with actor, inputs and outcome | Qualitative — new capability |

---

## 6. Other Value Levers

- **Knowledge retention.** TNB's grade-band adjacency and discipline rules now live in version-controlled code rather than in the heads of a few experienced HR practitioners. They survive transfers and retirements, and any change to them is reviewable.
- **Data sovereignty and security.** The application runs inside TNB's own Databricks workspace, against a workspace-hosted model serving endpoint authenticated by workspace identity. No employee talent data, KPI history or personal record is sent to an external vendor cloud or third-party API — a materially different risk posture from any SaaS HCM alternative.
- **Risk reduction.** Deterministic pre-filtering means a candidate is never recommended outside their eligible grade band or required discipline, regardless of model behaviour. AI output is confined to ranking and explanation.
- **Compliance and auditability.** The `audit_logs` table gives a defensible answer to "why was this person shortlisted and that person not", which matters for both internal governance and any external challenge.
- **Standardisation.** One shortlisting method applied identically across projects and reviewers, replacing per-reviewer judgement about what counts as a relevant qualification.
- **Vendor independence.** No per-employee licence, no seat count, no renewal negotiation. Scaling to more positions or more staff costs nothing additional.
- **Scalability.** Vector-ready schema and chunked ingestion are already in place, so semantic search over the document corpus is an increment rather than a rebuild.
- **Talent uplift.** The build leaves TNB with in-house capability in FastAPI, React and Databricks model serving, applicable to the next internal AI use case.

---

## 7. Readiness & Next Steps

Stated plainly, so the framework is not mistaken for a production sign-off:

- **Authentication is a placeholder.** A single hardcoded credential pair returns a static token, and no API endpoint validates it; CORS is currently open. Integrating Databricks Apps identity or TNB SSO, and enforcing it per endpoint, is the prerequisite for handling live talent data.
- **Role-based access is not yet implemented.** Every authenticated user has full rights, including deletion. HR reviewer, project owner and administrator roles should be separated before wider rollout.
- **The vector tables are provisioned but unused.** Semantic retrieval over job descriptions and talent cards is the natural next increment.
- **Validate the estimates in sections 3–5** against project records and the HR baseline before this document is used in a business case.

---

**Headline for stakeholders:** *An internally built succession engine that encodes TNB's own promotion rules in software, cuts position shortlisting from days to seconds, and keeps every talent record inside TNB's tenant — at a one-off build cost in the region of RM 126,000–237,000 rather than a perpetual per-employee licence.*
