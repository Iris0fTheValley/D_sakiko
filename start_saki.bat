@echo off
setlocal
set "WORD=runtime"

echo ========================================
echo   Saki Electron + Qt 双窗口模式
echo   Qt 聊天输入 + Electron Live2D 渲染
echo ========================================
echo.

REM 启动后端（Qt 聊天界面 + Bridge）
pushd "%~dp0..\DSakiko3.10\GPT_SoVITS"
set SAKI_ELECTRON_MODE=1
start "Saki Backend (Qt)" cmd /k ""..\%WORD%\python.exe" main2.py"
popd

REM 等待后端初始化
echo 等待后端初始化 (10s)...
timeout /t 10 /nobreak >nul

REM 启动 Electron Live2D 窗口
pushd "%~dp0electron_frontend"
start "Saki Electron Live2D" cmd /k "npx electron-vite dev"
popd

echo.
echo ========================================
echo  启动完成！两个窗口：
echo    - Saki Backend (Qt): 聊天输入
echo    - Saki Electron Live2D: 角色渲染
echo  关闭 Qt 窗口即可停止所有进程
echo ========================================
pause
