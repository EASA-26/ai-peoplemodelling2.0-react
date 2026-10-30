# Deploying People Modelling.AI to gencodev.tnb.com.my

Step-by-step, for TNBDEVIHDAAPP (`10.250.53.90`). The end state is the same shape as
Hana (FazuraGPT): the app runs as a scheduled task behind IIS, and **a push to GitHub
deploys itself within five minutes** with a health gate and an automatic rollback.

> **Read this first.** The box is shared with about twenty teams. FazuraGPT (5174),
> Talent Portal (8011), an IAR app (**8001 — never touch**), another team's backend
> (9000), coalrating, PI Data Extraction, SOA Ageing and PostgreSQL all live here.
> Nothing in this guide binds their ports, restarts their tasks, or runs `iisreset`.
> Step 7 is the only one that touches shared infrastructure, and it needs a human
> decision before it runs.

---

## What you end up with

```
  you: git push
       |
       v
  GitHub  EASA-26/ai-peoplemodelling2.0-react  (public, branch: main)
       |
       |  polled every 5 min by task PeopleModelling-AutoDeploy (SYSTEM)
       v
  D:\EASA\PeopleModelling              scripts\auto-deploy.ps1
       |                                 pull -> verify hash moved -> build
       |                                 -> restart if backend changed
       |                                 -> verify app answers ON THE NEW COMMIT
       |                                 -> roll back and HOLD if it does not
       v
  task PeopleModelling (SYSTEM)  ->  127.0.0.1:8021  (loopback only)
       ^
       |  reverse proxy
  IIS "Default Web Site" application /peoplemodelling
       ^
       |
  https://gencodev.tnb.com.my/peoplemodelling/
```

| Setting | Value |
|---|---|
| App folder | `D:\EASA\PeopleModelling` |
| Loopback port | `8021` (set in `.env.local`, read by everything else) |
| Public URL | `https://gencodev.tnb.com.my/peoplemodelling/` |
| App task | `PeopleModelling` (SYSTEM, at startup, restarts on failure) |
| Deploy task | `PeopleModelling-AutoDeploy` (SYSTEM, every 5 min) |
| Python | `D:\EASA\PeopleModelling\.venv\Scripts\python.exe` — **its own venv**, always the full path |
| Database | SQLite at `D:\EASA\PeopleModelling\.app_state\hr_ai.db` |
| Logs | `logs\app.log` (app), `logs\deploy.log` (deploys) |

---

## Test the sub-path *before* you touch the server

The sub-path is where this deployment can quietly break: assets, routing and API calls
all have to resolve under `/peoplemodelling`. `scripts\simulate-iis.py` reproduces
exactly what the IIS `web.config` does — the add-slash redirect, and proxying with the
prefix **stripped** — so you can prove the whole chain on your own machine.

```bash
cd frontend && MSYS_NO_PATHCONV=1 APP_BASE_PATH=/peoplemodelling/ npm run build && cd ..
```

```bash
python run_server.py
```

```bash
python scripts/simulate-iis.py
```

Then open `http://127.0.0.1:8099/peoplemodelling/` and sign in. What you are checking:
the login page renders, the URL becomes `/peoplemodelling/analytics`, the nav links are
`/peoplemodelling/...`, and the simulator's own log shows
`GET /peoplemodelling/api/analytics/summary 200`.

This caught two real bugs before they reached the server: a `fetch("/api/auth/login")`
that bypassed the axios base URL, and `window.location.href = "/"` redirects that left
the application entirely. Re-run it after any change to routing, navigation or API calls.

---

## Before you start

Three things are **not** mine to do and must be in hand:

1. **Server access** — the `tnb\ada.services` domain password. Shared account; you type
   it at the prompt. Never stored, never echoed, never in a script.
2. **A Databricks personal access token** — generated in your workspace. You paste it
   into `.env.local` on the server yourself (step 4). It must not travel through chat,
   a command line, a script or a commit.
3. **The OK of the owner of "Default Web Site"** — step 7 adds one application to their
   shared site. It changes nothing of theirs, but it is their site. Get the yes first.

One-time on your own laptop, in an **elevated** PowerShell:

```powershell
Start-Service WinRM
Set-Item WSMan:\localhost\Client\TrustedHosts "10.250.53.90" -Concatenate -Force
```

---

## Step 1 — Connect

```powershell
Enter-PSSession -ComputerName 10.250.53.90 -Credential tnb\ada.services
```

Prefer this to RDP: it carries the full admin token (so `*-ScheduledTask` and
machine-wide `pip` work without the RDP elevation dance), several people can be
connected at once, and it stays usable when the box is thrashing.

Everything below runs inside that session. RDP is only needed for a GUI installer or a
browser **on** the server — not for any step here.

---

## Step 2 — Clone the repo

```powershell
New-Item -ItemType Directory -Path D:\EASA -Force | Out-Null
git clone https://github.com/EASA-26/ai-peoplemodelling2.0-react.git D:\EASA\PeopleModelling
```

The repo is **public**, so this needs no credential at all. Never embed a token in the
URL.

> If `git clone` appears to hang forever, that is the WinRM pipe deadlock, not the
> network: git writes progress to stderr, remoting does not drain it, the buffer fills
> and git blocks. `scripts\auto-deploy.ps1` already works around it. For a one-off
> clone, add `--quiet`, or run the clone from an RDP window.

Confirm which branch you are on — **read it, never type it.** Deploying `main` into a
checkout that is on another branch is a no-op that looks like success:

```powershell
git -C D:\EASA\PeopleModelling rev-parse --abbrev-ref HEAD
```

---

## Step 3 — Pick the port, and prove it is free

```powershell
Get-NetTCPConnection -LocalPort 8021 -State Listen -ErrorAction SilentlyContinue
```

**No output is what you want.** If something answers, pick another free port and use it
consistently in steps 4 and 7 — do **not** kill the owner. Several apps here are Python,
and a stale process is a human's call, not yours.

---

## Step 4 — Create `.env.local`

```powershell
Copy-Item D:\EASA\PeopleModelling\.env.local.example D:\EASA\PeopleModelling\.env.local
notepad D:\EASA\PeopleModelling\.env.local
```

Set `PORT=8021`, set `DATABRICKS_HOST`, and **paste your token into `DATABRICKS_TOKEN`
yourself.** Format rules, each of which has cost somebody an afternoon:

- `KEY=value` — no quotes, no spaces around `=`, no `#` inside a value.
- The file must be named exactly `.env.local`. Notepad's *Save As* silently writes
  `.env.local.txt`, which is ignored without a word.

```powershell
dir D:\EASA\PeopleModelling\.env*     # must show .env.local, NOT .env.local.txt
```

The app reads this once at startup, so **restart the task after any later edit.**

> Leaving `DATABRICKS_TOKEN` empty is a valid way to start: uploads, the rules-based
> shortlist, person-to-position, analytics, audit and export all work without it. Only
> the AI readiness column fails. Add it later and restart.

---

## Step 5 — Provision (dry run first)

```powershell
powershell -ExecutionPolicy Bypass -File D:\EASA\PeopleModelling\scripts\setup-server.ps1
```

This **changes nothing.** It checks python, npm, git, elevation, that the port is free,
that `.env.local` agrees with the port you chose, and prints exactly what it would do.
Fix anything it marks `FAIL` before going on.

When it is clean:

```powershell
powershell -ExecutionPolicy Bypass -File D:\EASA\PeopleModelling\scripts\setup-server.ps1 -Apply
```

It creates the folders, writes the launcher, creates `.venv` and installs the backend
requirements **into it**, builds the frontend with `APP_BASE_PATH=/peoplemodelling/`,
creates both scheduled tasks, and starts the app.

> **Why its own virtual environment, when FazuraGPT uses the machine-wide one.**
> `C:\Python314` is shared by FazuraGPT, the Talent Portal, coalrating, PI Data
> Extraction and SOA Ageing. This app's requirements disagree with theirs —
> `databricks-sdk` needs a newer `certifi` than they have — so installing machine-wide
> would change the CA bundle four other teams trust. And the deploy here is
> **unattended, every five minutes**: every future `requirements.txt` change would put
> that same question to a script running at 3am. The venv makes it impossible for this
> app to disturb theirs. It costs about 400 MB and slightly slower `pip` runs.
>
> This was learned the hard way during the first deploy: `pip install
> --ignore-installed` (taken from the Hana runbook, where it solves a different problem)
> tried to reinstall numpy, which a running app had loaded, and failed with
> `[WinError 5] Access is denied` **while already elevated**. A locked `.pyd` is a file
> in use, not a permissions problem — and a flag that reaches into other teams' packages
> is wrong on a shared box however it is spelled.

---

## Step 6 — Verify it answers, locally on the server

```powershell
powershell -ExecutionPolicy Bypass -File D:\EASA\PeopleModelling\scripts\check-health.ps1
```

You want `identity  people-modelling (this IS our app)` and a `commit` matching
`git rev-parse HEAD`.

> **Do not use `Invoke-WebRequest`, `curl.exe` or .NET `HttpClient` here.** All three
> hang on this box, even against `127.0.0.1`. `check-health.ps1` uses a raw socket,
> which answers in about 100 ms. Anyone "fixing" it to use `Invoke-WebRequest` will
> reintroduce the hang.

The identity check is not ceremony: ports get reused on a shared box, and another
project's dev server answering on yours reads as a healthy deploy unless you check
*who* replied.

---

## Step 7 — Give it its public address *(needs the site owner's OK)*

This is the one step that touches shared infrastructure. **A human runs this, never an
agent**, and only after the owner of "Default Web Site" has said yes.

First the application pool — ours, so a problem in it cannot affect another team:

```powershell
C:\Windows\system32\inetsrv\appcmd.exe add apppool /name:PeopleModellingPool /managedRuntimeVersion:"" /startMode:AlwaysRunning
```

`managedRuntimeVersion:""` means "No Managed Code": nothing .NET runs in it, IIS is only
reverse-proxying.

Then the application itself. Reuse FazuraGPT's proven script — it is fully
parameterised, backs up `applicationHost.config`, verifies that nothing else on the site
changed, and **removes its own application again if anything did**:

```powershell
powershell -ExecutionPolicy Bypass -File D:\EASA\Fazura\FazuraGPT\scripts\setup-hana-path.ps1 -AppPath /peoplemodelling -AppPool PeopleModellingPool -Backend http://127.0.0.1:8021 -Folder D:\EASA\PeopleModelling\iis-app -BackupRoot D:\EASA\PeopleModelling\iis-backup -MaintenancePage D:\EASA\PeopleModelling\public\maintenance.html
```

That is the **dry run** — it prints the `web.config` it would write and changes nothing.
Read it, then add `-Apply`.

Two things that `web.config` does, both load-bearing:

- **`<clear />` must stay.** Default Web Site's own `web.config` sends `^api/(.*)` to
  another team's backend on port 9000, and inherited rules are re-evaluated relative to
  each application. Without `<clear />`, every `/peoplemodelling/api/...` call would be
  handed to them.
- **The `/peoplemodelling` prefix is stripped** before the request reaches the app, so
  the backend keeps its routes at the root. That is why the frontend is built with
  `APP_BASE_PATH=/peoplemodelling/` while the Python side needs no path configuration at
  all.

Undo, if needed: `scripts\rollback-hana-path.ps1` in the FazuraGPT repo, with the same
`-AppPath`.

---

## Step 8 — Verify from a laptop

Open `https://gencodev.tnb.com.my/peoplemodelling/` and hard-refresh.

Reachable on the internal network only. A personal laptop may need a hosts entry
`10.250.53.90 gencodev.tnb.com.my` and Chrome's "Secure DNS" turned off.

Check in order:

1. The login page renders (so IIS → app → `dist` all work).
2. Sign in with `admin` / `genco2025`.
3. The dashboard loads data (so `/peoplemodelling/api/...` reached **our** app and not
   port 9000 — if `<clear />` were missing, this is exactly where it would fail).
4. Upload a small file in Data Management (so the storage root is writable).
5. Run a succession recommendation (so the Databricks token works). If the shortlist
   appears but the AI column is empty, the rules worked and the LLM call failed — check
   the token and `ENDPOINT_NAME`.

---

## Step 9 — Prove the CI/CD loop

The point of all this. From your laptop:

```bash
git commit --allow-empty -m "Test the auto-deploy" && git push
```

Then on the server, within five minutes:

```powershell
Get-Content D:\EASA\PeopleModelling\logs\deploy.log -Tail 30
```

You should see the branch read, the hash move, the build, and — only if the backend
changed — a restart followed by `app answered 200 on 8021, Ns after the start`.

To watch a run happen now rather than wait:

```powershell
Start-ScheduledTask -TaskName PeopleModelling-AutoDeploy
```

---

## Operating it

### What the auto-deploy does on every run

**pull → verify the hash moved → build → restart → verify the app answers on the new
commit.** A build after a failed pull "succeeds" by rebuilding old code; that produced a
convincing fake deploy on FazuraGPT twice, which is why the hash is checked before the
build and the *running app's own commit* is checked after the restart.

- **A frontend-only change needs no restart.** The app serves `frontend\dist` off disk,
  so a rebuilt asset is live the moment it is written — zero interruption.
- **A backend change restarts the task**, and IIS shows `maintenance.html` for the few
  seconds it takes.
- **A failed deploy rolls back** to the previous commit and records the failure.

### A commit that failed is held, not retried

Without this, a rollback puts the checkout back while GitHub still holds the bad commit,
so the next run deploys it again — every five minutes. FazuraGPT did exactly that three
times in ten minutes before the hold existed.

So a rollback writes `logs\deploy.failed.json`, and later runs log
`not retrying <hash>: it failed at <time> (<reason>)` and leave it alone — while still
reviving the app if it is down. To lift the hold: **push a fix or a revert** (any other
commit deploys normally), or delete `logs\deploy.failed.json`, or run with `-Force`.
Deleting that file is the operator's call, never an agent's.

### Everyday commands

```powershell
powershell -File D:\EASA\PeopleModelling\scripts\check-health.ps1          # is it up, on what commit
Get-Content D:\EASA\PeopleModelling\logs\deploy.log -Tail 40               # what the last deploys did
Get-Content D:\EASA\PeopleModelling\logs\app.log -Tail 40                  # what the app said
powershell -File D:\EASA\PeopleModelling\scripts\auto-deploy.ps1 -WhatIf   # what a deploy would do now
```

Restart by hand:

```powershell
Stop-ScheduledTask -TaskName PeopleModelling; Start-Sleep -Seconds 3; Start-ScheduledTask -TaskName PeopleModelling
```

### Failure table

| Symptom | Meaning | Action |
|---|---|---|
| 502 at `/peoplemodelling/` | Backend process is down; IIS is fine | `check-health.ps1`; read `logs\app.log`; start the task |
| `not retrying <hash>` in `deploy.log` | That commit failed and is being held | Read the first `ERROR` above the rollback; push a fix or a revert |
| App up, but a feature fails while standalone tests pass | A package is missing from the venv, or you tested with the wrong interpreter | Test with `.venv\Scripts\python.exe`, **not** `C:\Python314\python.exe`. Then `.venv\Scripts\python.exe -m pip install -r backend\requirements.txt` and restart |
| `[WinError 5] Access is denied` on a `.pyd` during pip, while elevated | A **file in use** — a running app has that DLL mapped — not a permissions problem | Never force it. Inside the venv this should not happen; if it does, stop the app task first. Never use `--ignore-installed` on `C:\Python314` |
| Task `LastTaskResult` = 267009 | **RUNNING.** This is success, not an error | — |
| Task result 1 and no log at all | Launcher missing, or a bare `python` instead of the full path | Re-run `setup-server.ps1 -Apply` |
| `could not bind port 8021` in `app.log` | Another copy is still running | Find the owner of 8021 and stop *that* — never a blind kill |
| Dashboard loads but every API call 404s or returns someone else's data | `<clear />` is missing from the IIS `web.config` | Re-run step 7; `^api/(.*)` is being inherited and sent to port 9000 |
| `MemoryError` anywhere | The **box** is memory-starved (shared, ~6 apps) | Check top consumers; if it recurs, escalate for RAM — and check PostgreSQL survived, since its death silently takes the Talent Portal with it |
| Uploads vanish after a reboot | Storage resolved to a temp path | Confirm `HR_PEOPLE_APP_STORAGE_ROOT` in `.env.local`, then `GET /api/health/storage` |
| `git pull` refuses: "local changes would be overwritten by merge" | The repo **tracks** `frontend/dist`, and the server's own build rewrites it | Already handled: `auto-deploy.ps1` restores and cleans `frontend/dist` before every pull. If you hit this running git by hand, do the same |
| Assets 404, `index.html` points at `/Program Files/Git/...` | Git Bash's MSYS path translation mangled `APP_BASE_PATH` | Build from PowerShell, or prefix with `MSYS_NO_PATHCONV=1`. `auto-deploy.ps1` sets the variable through .NET, so it is unaffected |
| `MODULE_NOT_FOUND: @rollup/rollup-win32-x64-msvc` during a build | The known npm optional-dependency bug | `npm install @rollup/rollup-win32-x64-msvc --no-save`, or delete `node_modules` and `package-lock.json` and reinstall |

---

## What this deployment does *not* give you

Stated plainly so nobody assumes otherwise:

- **There is no real authentication.** A single hardcoded credential pair returns a
  placeholder token that **no endpoint validates**, and CORS is open. Anyone on the
  internal network who can reach the URL can call any endpoint directly, including the
  bulk-delete routes. Before this holds live talent data, put IIS Windows
  authentication in front of it (the `AUTH_MODE=header` pattern FazuraGPT uses, where
  IIS forwards `{LOGON_USER}` and the app trusts it *only* from loopback), and enforce
  it per endpoint.
- **No role separation.** Every user who gets in has full rights.
- **No backup of the SQLite database.** `.app_state\hr_ai.db` is not backed up by
  anything. Add a scheduled copy before it holds data anyone depends on.
- **No HTTPS between IIS and the app** — by design. The app binds loopback only, so the
  hop never leaves the machine.
