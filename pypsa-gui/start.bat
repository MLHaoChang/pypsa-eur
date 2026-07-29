@echo off
setlocal

REM Windows counterpart to start.sh. Starts the FastAPI backend (:8000) and the
REM Vite dev server (:5173), each in its own console window.
REM
REM Node is NOT assumed to live in pypsa-gui\.nodeenv any more: nothing in the
REM repo creates that directory and it is gitignored, so on a fresh clone the
REM old hard-coded path silently produced a frontend window that died instantly.
REM npm is now discovered — .nodeenv first (so existing setups keep working),
REM then the pixi environment, then whatever is on PATH.

REM Paths relative to pypsa-eur root
set ROOT=%~dp0..
set PIXI_ENV=%ROOT%\.pixi\envs\default
set PYTHON=%PIXI_ENV%\python.exe
set BACKEND=%ROOT%\pypsa-gui\backend
set FRONTEND=%ROOT%\pypsa-gui\frontend

if not exist "%PYTHON%" (
    echo error: pixi environment not found at %PIXI_ENV%
    echo        run "pixi install" from %ROOT% first.
    exit /b 1
)

REM Locate npm. Each candidate is a directory that may contain npm.cmd; the
REM first hit wins and its directory goes on PATH for the frontend window.
set NPM=
set NPMDIR=
for %%D in (
    "%ROOT%\pypsa-gui\.nodeenv\Scripts"
    "%PIXI_ENV%"
    "%PIXI_ENV%\Library\bin"
    "%PIXI_ENV%\Scripts"
) do (
    if not defined NPM if exist "%%~D\npm.cmd" (
        set "NPM=%%~D\npm.cmd"
        set "NPMDIR=%%~D"
    )
)

if not defined NPM (
    for /f "delims=" %%P in ('where npm 2^>nul') do (
        if not defined NPM (
            set "NPM=%%~fP"
            set "NPMDIR=%%~dpP"
        )
    )
)

if not defined NPM (
    echo error: npm not found.
    echo        Looked in pypsa-gui\.nodeenv\Scripts, the pixi environment at
    echo        %PIXI_ENV%, and PATH.
    echo        Install Node 22+ or run "pixi install" from %ROOT%.
    exit /b 1
)

if not exist "%FRONTEND%\node_modules" (
    echo note: frontend dependencies missing - running npm install once...
    pushd "%FRONTEND%"
    call "%NPM%" install
    if errorlevel 1 (
        echo error: npm install failed.
        popd
        exit /b 1
    )
    popd
)

if not exist "%FRONTEND%\index.html" (
    echo warning: %FRONTEND%\index.html is missing, so Vite has no entry point
    echo          and the page will not load. The root .gitignore excludes it
    echo          via the "*.html" rule - it needs to be un-ignored and
    echo          committed before a fresh clone can run the front end.
    echo.
)

echo Starting PyPSA GUI...
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:5173
echo.

REM Start backend in a new window
start "PyPSA-GUI Backend" /D "%BACKEND%" cmd /k ""%PYTHON%" -m uvicorn main:app --host 0.0.0.0 --reload --port 8000"

REM Wait a moment then start frontend
timeout /t 2 /nobreak >nul

REM Start frontend in a new window
start "PyPSA-GUI Frontend" cmd /k "set PATH=%NPMDIR%;%PATH% && cd /d "%FRONTEND%" && "%NPM%" run dev"

echo Both services starting. Open http://localhost:5173 in your browser.
