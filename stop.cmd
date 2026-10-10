@echo off
rem Stop the local-sounds server (and its engine workers), release their VRAM and close
rem the server's window.
rem
rem Closing the server's window does the same. Use this when you'd rather not hunt for the
rem window, or from a script or an agent.
rem
rem The server writes its PID to output\.server.pid; this stops that process tree,
rem whichever path (this folder or a junction to it) started it. As a fallback it
rem also stops python processes running server\app.py or server\workers\*_worker.py
rem from this folder. Other python programs are left alone. Before stopping anything it
rem leaves output\.stop-requested, which tells the window to close at once instead of
rem showing "the server has stopped" for a minute.
rem
rem Exit code: 0 stopped (or nothing was running), 1 something is still running.

setlocal
set "LS_HERE=%~dp0"
echo Looking for a running local-sounds server...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$here = $env:LS_HERE.TrimEnd('\'); $n = 0;" ^
  "$out = Join-Path $here 'output'; $pidFile = Join-Path $out '.server.pid';" ^
  "$mark = { if (Test-Path $out) { Set-Content -LiteralPath (Join-Path $out '.stop-requested') -Value 'stop' } };" ^
  "$roots = @($here); $t = (Get-Item -LiteralPath $here).Target;" ^
  "if ($t) { $roots += @($t | ForEach-Object { $_.TrimEnd('\') }) };" ^
  "$mine = { Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {" ^
  "  $c = $_.CommandLine; $c -and ($c -match 'server.(app|workers.\w+_worker)\.py') -and" ^
  "  @($roots | Where-Object { $c.IndexOf($_, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count } };" ^
  "if (Test-Path $pidFile) {" ^
  "  $id = 0; [int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref]$id) | Out-Null;" ^
  "  $proc = Get-CimInstance Win32_Process -Filter \"ProcessId=$id\";" ^
  "  if ($proc -and $proc.CommandLine -match 'server.app\.py') {" ^
  "    & $mark;" ^
  "    Write-Host ('  stopping server PID {0} and its engine workers' -f $id) -ForegroundColor Yellow;" ^
  "    taskkill /T /F /PID $id | Out-Null; $n++ };" ^
  "  Remove-Item $pidFile -ErrorAction SilentlyContinue };" ^
  "Start-Sleep -Milliseconds 500;" ^
  "$p = & $mine;" ^
  "if ($p -and -not $n) { & $mark };" ^
  "foreach ($x in $p) {" ^
  "  Write-Host ('  stopping PID {0}' -f $x.ProcessId) -ForegroundColor Yellow;" ^
  "  Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue; $n++ };" ^
  "if (-not $n) { Write-Host '  Nothing running.' -ForegroundColor DarkGray; exit 0 };" ^
  "Start-Sleep -Seconds 2;" ^
  "if (& $mine) { Write-Host '  Something is still running: close the server window, or use Task Manager.' -ForegroundColor Red; exit 1 };" ^
  "Write-Host '  Stopped.' -ForegroundColor Green"
set "FAILED=%errorlevel%"

if exist "%SystemRoot%\System32\nvidia-smi.exe" (
  echo.
  echo VRAM now in use:
  "%SystemRoot%\System32\nvidia-smi.exe" --query-gpu=memory.used,memory.total --format=csv,noheader
)

rem Report whether anything survived, so a caller can tell: reading the VRAM above would
rem otherwise overwrite the exit code with its own.
endlocal & exit /b %FAILED%
