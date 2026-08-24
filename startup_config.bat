@echo off
setlocal
cd /d "%~dp0"

if exist "runtime\python.exe" (
    "runtime\python.exe" "GPT_SoVITS\dsakiko_configuration.py"
) else (
    ".venv\Scripts\python.exe" "GPT_SoVITS\dsakiko_configuration.py"
)

pause
