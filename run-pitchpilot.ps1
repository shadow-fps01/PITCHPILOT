#!/usr/bin/env pwsh
# PitchPilot one-command launcher: starts the FastAPI + Vite frontend AND the
# Electron desktop overlay together.
#
# Usage: pwsh run-pitchpilot.ps1
#   - frontend : http://localhost:5173
#   - backend  : http://localhost:8000 (auto-started by the frontend dev server)
#   - desktop  : protected Electron overlay window (view=live)
#
# Ctrl+C stops all three processes.

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# Resolve directories relative to this script
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$frontendDir = Join-Path $scriptDir "frontend"
$desktopDir  = Join-Path $scriptDir "desktop"

# Helpers
$childProcesses = @()

function cleanup {
    Write-Host "`n[launcher] shutting down all child processes..." -ForegroundColor Yellow
    foreach ($proc in $childProcesses) {
        try {
            if ($proc -ne $null -and -not $proc.HasExited) {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
    # Last resort: clean up anything we launched by name
    Get-Process -Name "vite","electron","uvicorn" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "*$scriptDir*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "[launcher] all stopped." -ForegroundColor Green
}

# Trap Ctrl+C and script exit so children are always reaped.
trap { cleanup; exit 130 }
Register-EngineEvent PowerShell.Exiting -Action { cleanup } | Out-Null

# 1. Start the frontend dev server (which also starts the backend)
Write-Host "[launcher] starting frontend + FastAPI backend (frontend/)" -ForegroundColor Cyan
$vite = Start-Process -FilePath "npm.cmd" -ArgumentList "run","dev" -WorkingDirectory $frontendDir -NoNewWindow -PassThru
$childProcesses += $vite

# Wait until Vite is accepting connections (it proxies /api to :8000).
Write-Host "[launcher] waiting for http://localhost:5173 ..."
$frontendReady = $false
for ($i = 0; $i -lt 120; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:5173/" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($resp -ne $null) { $frontendReady = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 500
}

if (-not $frontendReady) {
    Write-Host "[launcher] ERROR: frontend did not become ready in 60s" -ForegroundColor Red
    cleanup
    exit 1
}

# Give the backend (auto-started by vite) a moment to bind :8000.
Start-Sleep -Seconds 3
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
    if ($h) { Write-Host "[launcher] FastAPI backend OK: " + $h.status -ForegroundColor Green }
} catch {}

# 2. Start the Electron desktop overlay
Write-Host "[launcher] starting desktop overlay ($desktopDir)" -ForegroundColor Cyan
$npmCmd = if (Test-Path "$env:ProgramFiles\nodejs\npm.cmd") { "npm.cmd" } else { "npm" }
$electron = Start-Process -FilePath "npm.cmd" -ArgumentList "start" -WorkingDirectory $desktopDir -NoNewWindow -PassThru
$childProcesses += $electron

Write-Host "`n[launcher] everything is running:" -ForegroundColor Green
Write-Host "   frontend  : http://localhost:5173"
Write-Host "   backend   : http://localhost:8000 (auto-proxied by Vite)"
Write-Host "   desktop   : protected overlay window (Electron)" -ForegroundColor Green
Write-Host "`nPress Ctrl+C to stop all three." -ForegroundColor DarkGray

# Keep the launcher alive while children run.
while ($true) {
    Start-Sleep -Seconds 2
    if ($vite.HasExited) {
        Write-Host "[launcher] frontend process exited; stopping all." -ForegroundColor Red
        cleanup
        exit 1
    }
    if ($electron.HasExited) {
        Write-Host "[launcher] desktop process exited; stopping all." -ForegroundColor Yellow
        cleanup
        exit 1
    }
}
