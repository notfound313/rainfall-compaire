param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
$Python = Join-Path (Split-Path $PSScriptRoot -Parent) ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtual environment tidak ditemukan: $Python"
}
& $Python -m uvicorn app.main:app --app-dir $PSScriptRoot --host 127.0.0.1 --port $Port
