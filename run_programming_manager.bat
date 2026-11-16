@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "HITMO_PYTHON=.venv\Scripts\python.exe"
) else (
    set "HITMO_PYTHON=python"
)
"%HITMO_PYTHON%" -m hitmo.programming_manager_app
if errorlevel 1 (
    echo.
    echo O modulo de programacao encerrou com erro. Confira Python, dependencias e conexao.
    pause
)
endlocal
