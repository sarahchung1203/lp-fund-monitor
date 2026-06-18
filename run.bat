@echo off
REM ============================================================
REM  LP fund-notice monitor - MANUAL / on-demand refresh
REM  (Daily auto-updates run in the cloud via GitHub Actions;
REM   this script is for refreshing on this PC whenever you want.)
REM ============================================================
cd /d "%~dp0"

REM 0) sync with the cloud first so we never diverge from CI
git rev-parse --is-inside-work-tree >nul 2>&1
if %errorlevel%==0 (
  git pull --rebase --autostash >> build.log 2>&1
)

REM 1) scrape all sites + regenerate index.html
python build.py >> build.log 2>&1

REM 2) push the refresh up (keeps the hosted phone version in sync)
if %errorlevel%==0 if exist .git (
  git add -A
  git commit -m "manual update" >nul 2>&1
  git push >> build.log 2>&1
)
