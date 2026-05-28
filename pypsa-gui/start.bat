@echo off
setlocal

REM Paths relative to pypsa-eur root
set ROOT=%~dp0..
set PYTHON=%ROOT%\.pixi\envs\default\python.exe
set NODE=%ROOT%\pypsa-gui\.nodeenv\Scripts\node.exe
set NPM=%ROOT%\pypsa-gui\.nodeenv\Scripts\npm.cmd
set BACKEND=%ROOT%\pypsa-gui\backend
set FRONTEND=%ROOT%\pypsa-gui\frontend

echo Starting PyPSA GUI...
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:5173
echo.

REM Start backend in a new window
start "PyPSA-GUI Backend" /D "%BACKEND%" cmd /k ""%PYTHON%" -m uvicorn main:app --host 0.0.0.0 --reload --port 8000"

REM Wait a moment then start frontend
timeout /t 2 /nobreak >nul

REM Start frontend in a new window
start "PyPSA-GUI Frontend" cmd /k "set PATH=%ROOT%\pypsa-gui\.nodeenv\Scripts;%PATH% && cd /d "%FRONTEND%" && "%NPM%" run dev"

echo Both services starting. Open http://localhost:5173 in your browser.
