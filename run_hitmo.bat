@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo HitMo Radio Studio nao instalado. Consulte README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m hitmo.app

