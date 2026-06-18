@echo off
REM ============================================================
REM  LP fund-notice monitor - daily runner (Task Scheduler)
REM ============================================================
cd /d "%~dp0"

REM 1) scrape all sites + regenerate index.html
python build.py >> build.log 2>&1

REM 2) if a git repo is configured, auto-deploy to GitHub Pages
git rev-parse --is-inside-work-tree >nul 2>&1
if %errorlevel%==0 (
  git add -A
  git commit -m "auto-update" >nul 2>&1
  git push >nul 2>&1
)
