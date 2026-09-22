# =========================================
# IZgoN - Windows Setup Script (PowerShell)
# =========================================

Write-Host ""
Write-Host "  ██╗███████╗ ██████╗  ██████╗ ███╗   ██╗" -ForegroundColor Cyan
Write-Host "  ██║╚════██║██╔════╝ ██╔════╝ ████╗  ██║" -ForegroundColor Cyan
Write-Host "  ██║    ██╔╝██║  ███╗██║  ███╗██╔██╗ ██║" -ForegroundColor Cyan
Write-Host "  ██║   ██╔╝ ██║   ██║██║   ██║██║╚██╗██║" -ForegroundColor Cyan
Write-Host "  ██║  ██╔╝  ╚██████╔╝╚██████╔╝██║ ╚████║" -ForegroundColor Cyan
Write-Host "  ╚═╝  ╚═╝    ╚═════╝  ╚═════╝ ╚═╝  ╚═══╝" -ForegroundColor Cyan
Write-Host ""
Write-Host "  IZgoN - Send Only What Changed" -ForegroundColor Yellow
Write-Host "  ════════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host ""

# Check Docker
Write-Host "[1/4] Checking Docker..." -ForegroundColor Cyan
try {
    docker ps > $null 2>&1
    Write-Host "✓ Docker is running" -ForegroundColor Green
} catch {
    Write-Host "✗ Docker is not running!" -ForegroundColor Red
    Write-Host "Please start Docker Desktop and try again." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# Generate .env
Write-Host ""
Write-Host "[2/4] Checking configuration..." -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    Write-Host "Generating .env file..." -ForegroundColor Yellow
    $secret = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 32 | ForEach-Object {[char]$_})
    
    @"
DATABASE_URL=sqlite:///./data/node_states.db
SECRET_KEY=$secret
DATAPULSE_API_KEY=$secret
DEBUG=False
PYTHONUNBUFFERED=1
"@ | Out-File -Encoding UTF8 ".env"
    Write-Host "✓ .env created with secure key" -ForegroundColor Green
} else {
    Write-Host "✓ .env already exists" -ForegroundColor Green
}

# Create data directory
Write-Host ""
Write-Host "[3/4] Preparing storage..." -ForegroundColor Cyan
if (-not (Test-Path "data")) {
    New-Item -ItemType Directory -Path "data" > $null
}
Write-Host "✓ Data directory ready" -ForegroundColor Green

# Start services
Write-Host ""
Write-Host "[4/4] Starting IZgoN..." -ForegroundColor Cyan
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Failed to start services." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "⏳ Waiting for dashboard to be ready..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

Write-Host ""
Write-Host "✓ IZgoN is running!" -ForegroundColor Green
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "Dashboard:    http://localhost:8000" -ForegroundColor Cyan
Write-Host "API Endpoint: http://localhost:8000/api" -ForegroundColor Cyan
Write-Host "Database:     ./data/node_states.db" -ForegroundColor Cyan
Write-Host "Config:       .env" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""

Write-Host "Opening dashboard in your browser..." -ForegroundColor Yellow
Start-Process "http://localhost:8000"

Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Yellow
Write-Host "  View logs:      docker compose logs -f" -ForegroundColor Gray
Write-Host "  Stop services:  docker compose down" -ForegroundColor Gray
Write-Host "  Restart:        docker compose restart" -ForegroundColor Gray
Write-Host ""

Read-Host "Press Enter to exit"
