@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [ProjectMind] Virtualenv not found at "%VENV_PY%".
    echo Create it with:
    echo     python -m venv "%SCRIPT_DIR%.venv"
    echo     "%SCRIPT_DIR%.venv\Scripts\pip.exe" install -e "%SCRIPT_DIR%"
    exit /b 1
)

"%VENV_PY%" "%SCRIPT_DIR%mcp_server.py" %*
