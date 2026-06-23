@echo off
setlocal
set "WORD=runtime"

echo ========================================
echo   Saki - Qt + Electron + Pygame
echo ========================================
echo.

REM Launch backend (Qt chat + Bridge + Pygame Live2D)
pushd "%~dp0..\DSakiko3.10\GPT_SoVITS"
set SAKI_ELECTRON_MODE=1
start "Saki Backend" cmd /k ""..\%WORD%\python.exe" main2.py"
popd

echo Waiting for backend init (10s)...
timeout /t 10 /nobreak >nul

REM Launch Electron Live2D window
pushd "%~dp0electron_frontend"
start "Saki Electron" cmd /k "npx electron-vite dev"
popd

echo.
echo ========================================
echo   Qt chat + Electron Live2D + Pygame Live2D
echo   Close Qt window to stop all.
echo ========================================
pause
