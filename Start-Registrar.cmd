@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python -m sedb_ral_public gui
if errorlevel 1 (
  echo.
  echo Could not start. Install Python 3.11+ with Tcl/Tk, then run: python -m pip install .
  pause
)
