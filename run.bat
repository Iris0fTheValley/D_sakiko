@echo off
setlocal
cd /d "%~dp0"
set LITELLM_LOCAL_MODEL_COST_MAP=True

if exist "runtime\python.exe" (
    "runtime\python.exe" "tools\launch_runtime.py"
) else (
    ".venv\Scripts\python.exe" "tools\launch_runtime.py"
)

pause
