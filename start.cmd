@echo off
rem Start the local-sounds server in its own window.
rem Close that window, or run stop.cmd, to stop the server and release the GPU.
rem
rem   start.cmd                 the server opens in its own window and this returns at once;
rem                             the page opens in your browser once it's ready
rem   start.cmd --no-browser    the same without opening the page (what agents use)
rem   start.cmd --engines sa3,moss   start both engines now (default: just the default one;
rem                             the other starts on its first request)
rem
rem The server only ever runs in that window, so it can't be left running out of sight: it
rem refuses to start anywhere else (a hidden background shell, python server\app.py).
setlocal
cd /d "%~dp0"
rem Captured before the option loop: it shifts the arguments.
set "SELF=%~f0"
set "HERE_DIR=%~dp0"

rem Windows' own timeout. Started from Git Bash, plain "timeout" can resolve to the GNU
rem coreutils one, which doesn't understand /t.
set "WAIT=%SystemRoot%\System32\timeout.exe"

if not exist "%HERE_DIR%.venv\Scripts\python.exe" (
  echo.
  echo No install found. Run install.ps1 first.
  echo.
  pause
  exit /b 1
)

set "BROWSER=1"
set "INWIN="
set "EXTRA="
set "ENG="
:args
if "%~1"=="" goto argsdone
if /i "%~1"=="--no-browser" (set "BROWSER=" & shift /1 & goto args)
if /i "%~1"=="--in-window"  (set "INWIN=1" & shift /1 & goto args)
rem cmd splits "sa3,moss" at the comma, so engine names are collected wherever they appear.
if /i "%~1"=="--engines"    (shift /1 & goto args)
if /i "%~1"=="sa3"          (set "ENG=%ENG%,sa3" & shift /1 & goto args)
if /i "%~1"=="moss"         (set "ENG=%ENG%,moss" & shift /1 & goto args)
echo Unknown option "%~1". Use:  start.cmd [--no-browser] [--engines sa3,moss]
exit /b 2
:argsdone
if defined ENG set "EXTRA=--engines %ENG:~1%"

rem Relaunch in a new, visible console and return immediately, so the server is always on
rem screen and closing its window shuts it down (Windows ends every process attached to a
rem console when the console closes). LOCAL_SOUNDS_WINDOW is how the server knows it is in
rem that window: the new console inherits it from here; nothing else sets it.
if defined INWIN goto run
set "PASS=%EXTRA%"
if not defined BROWSER set "PASS=--no-browser %EXTRA%"
set "LOCAL_SOUNDS_WINDOW=1"
start "local-sounds server - close this window to stop it" cmd /c ""%SELF%" --in-window %PASS%"
exit /b 0

:run
title local-sounds server - close this window to stop it
echo.
echo   local-sounds server on http://127.0.0.1:8191/
echo   Close this window, or run stop.cmd, to stop it and free the GPU.
echo.
rem A stop request left over from an earlier run must not close this window early.
del "%HERE_DIR%output\.stop-requested" 2>nul

rem Wait for the server to answer before opening the browser: the engine takes
rem a little while to load.
if defined BROWSER start "" /min cmd /c "for /l %%i in (1,1,150) do (curl.exe -s -o nul --max-time 2 http://127.0.0.1:8191/api/health && (start "" http://127.0.0.1:8191/ & exit /b) || "%WAIT%" /t 2 >nul)"

rem The server binds to 127.0.0.1 only (hard-coded in server\app.py).
"%HERE_DIR%.venv\Scripts\python.exe" "%HERE_DIR%server\app.py" --port 8191 %EXTRA%
set "CODE=%ERRORLEVEL%"

rem stop.cmd leaves this file before it stops the server: then the window closes at once
rem (exit code 0, so a Windows Terminal tab closes too). Any other end - a crash, a refused
rem start - stays on screen for a minute so its message can be read.
if exist "%HERE_DIR%output\.stop-requested" (
  del "%HERE_DIR%output\.stop-requested" 2>nul
  exit 0
)
echo.
echo   The server has stopped (exit code %CODE%). This window closes in 60 seconds;
echo   press a key to close it now.
"%WAIT%" /t 60 >nul
exit 0
