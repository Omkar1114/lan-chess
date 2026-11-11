# Run LAN Chess without Docker (Windows PowerShell).
# Creates a virtual environment on first use, loads .env and starts the server.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment in .venv ..."
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
}

if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        $line = $line.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { continue }
        $name, $value = $line -split "=", 2
        if ($null -ne $value) {
            [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
        }
    }
}

if (-not $env:STOCKFISH_PATH -and -not (Get-Command stockfish -ErrorAction SilentlyContinue)) {
    Write-Host "Note: Stockfish not found - play vs computer and analysis will be disabled."
    Write-Host "      Download it from https://stockfishchess.org/download/ and set STOCKFISH_PATH in .env"
}

& .\.venv\Scripts\python.exe -m app
