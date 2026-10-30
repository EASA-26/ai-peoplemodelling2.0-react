@echo off
rem The launcher the PeopleModelling scheduled task runs. The LIVE copy is the untracked
rem D:\EASA\PeopleModelling\run-peoplemodelling.cmd on the server -- it cannot live at the
rem repo root, because a pull refuses to overwrite an untracked file and that would wedge
rem the auto-deploy. This copy is the reference; keep the two the same.
rem
rem scripts\setup-server.ps1 writes the live copy from this one.
cd /d D:\EASA\PeopleModelling
if not exist logs mkdir logs
rem Unbuffered, so a line reaches the log when it happens rather than at the next 8KB
rem flush -- a log that trails reality is useless in the middle of an incident.
set PYTHONUNBUFFERED=1
rem The app's OWN interpreter, not C:\Python314. That one is shared with FazuraGPT, the
rem Talent Portal and three other apps, and this app's dependencies disagree with theirs
rem (databricks-sdk wants a newer certifi than they have). A virtual environment means
rem nothing this app installs can reach them, now or at any future deploy.
rem
rem ALWAYS the full path. A bare "python" is not on SYSTEM's PATH and the task then
rem fails with result 1 and no log at all.
"D:\EASA\PeopleModelling\.venv\Scripts\python.exe" run_server.py >> "D:\EASA\PeopleModelling\logs\app.log" 2>&1
