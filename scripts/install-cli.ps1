# install-cli.ps1  -  make `acr` available in EVERY terminal, forever.
#
# Creates a tiny launcher (%USERPROFILE%\.acr\bin\acr.cmd) that calls
# the project's venv, and adds that folder to your user PATH. After this, open
# a NEW terminal and type `acr` from any project  -  the interactive
# dashboard opens on the current directory (Claude-Code style).
#
# The launcher targets cmd/PowerShell (the user's shell); Git Bash users can
# call acr.exe directly or add a matching .sh shim.
#
# Usage (PowerShell, from the project root):
#   powershell -ExecutionPolicy Bypass -File scripts/install-cli.ps1

$ErrorActionPreference = "Stop"

# The script lives in <project>/scripts/, so the project root is its parent.
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$venvAcr = Join-Path $projectRoot ".venv\Scripts\acr.exe"

if (-not (Test-Path $venvAcr)) {
    Write-Host "ERROR: acr.exe not found at: $venvAcr" -ForegroundColor Red
    Write-Host "Run this script from inside the agentic-code-reviewer project (after `pip install -e .`)."
    exit 1
}

$binDir = Join-Path $HOME ".acr\bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

$shim = Join-Path $binDir "acr.cmd"
# Path contains spaces ("AI CODE"), so the exe must be quoted; %* forwards args.
$content = "@echo off`r`n`"$venvAcr`" %*`r`n"
Set-Content -Path $shim -Value $content -Encoding ascii

# Idempotent, containment-style PATH check (avoids wildcard edge cases).
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$alreadyOnPath = ($userPath -split ";") | Where-Object { $_ -eq $binDir }
if (-not $alreadyOnPath) {
    $newPath = ($userPath.TrimEnd(';') + ";" + $binDir)
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host ""
    Write-Host "DONE. `acr` is now installed globally." -ForegroundColor Green
    Write-Host "  1. Close this terminal and open a NEW one."
    Write-Host "  2. cd into any project and type:  acr"
    Write-Host "     (the interactive review dashboard opens on that folder)"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  acr                            # review current directory"
    Write-Host "  acr path/to/project            # review a specific folder"
    Write-Host "  acr --help                     # all commands"
    Write-Host "  acr review --repo o/r --pr 123"
} else {
    Write-Host "Already installed. Open a new terminal and type: acr"
}
