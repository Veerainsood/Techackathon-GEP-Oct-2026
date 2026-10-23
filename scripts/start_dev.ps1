# Starts the UDRE backend (FastAPI + event worker) and the React frontend for local development.
#   powershell -ExecutionPolicy Bypass -File scripts\start_dev.ps1
# Stop with Ctrl+C in each window; the embedded Postgres keeps running (python -m udre.pg_embedded stop).
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m udre.pg_embedded start
if (-not (Test-Path "frontend\node_modules")) { Push-Location frontend; npm install; Pop-Location }

Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root'; python -m uvicorn udre.api.app:app --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\frontend'; npm run dev -- --port 5173"
# HubSpot ingestion publishes sync events to the backend above (UDRE_EVENTS_URL / UDRE_EVENT_SECRET in its .env).
if (Test-Path "knowledge-ingestion\.env") {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\knowledge-ingestion'; python -m uvicorn knowledge_ingestion.api.app:app --port 8001"
    Write-Host "Ingestion: http://localhost:8001/docs"
}
Write-Host "Backend:  http://localhost:8000/docs"
Write-Host "Frontend: http://localhost:5173"
