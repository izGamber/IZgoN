# IzgoN - setup for Windows.
#
# Double-click IzgoN-Setup.cmd. This is the script it runs.
#
# What it does, and nothing else:
#   1. checks that Docker is installed and running
#   2. creates %USERPROFILE%\IzgoN
#   3. writes a compose file that pulls the published image - no build
#   4. generates a random API key into .env
#   5. starts IzgoN and Redis and waits until the service answers
#   6. puts an IzgoN icon on your Desktop and in the Start menu
#   7. opens the dashboard
#
# It installs nothing into Windows itself. Everything lives in that one folder
# plus two shortcuts. To remove it: run "docker compose down -v" in the folder,
# delete the folder, delete the two shortcuts.

$ErrorActionPreference = "Stop"
function Say([string]$Text, [string]$Colour = "Gray") { Write-Host $Text -ForegroundColor $Colour }

Say ""
Say "  IzgoN - setup" "Cyan"
Say "  --------------------------------------------------" "DarkGray"
Say ""

# --- 1. Docker ---------------------------------------------------------------
Say "  [1/6] Checking Docker..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Say ""
    Say "  Docker is not installed." "Yellow"
    Say ""
    Say "  IzgoN is a server and it needs Redis beside it. Docker runs both with"
    Say "  one command. Install it, restart the computer, then double-click"
    Say "  IzgoN-Setup.cmd again:"
    Say ""
    Say "      winget install -e --id Docker.DockerDesktop" "White"
    Say ""
    Read-Host "  Press Enter to close"
    exit 1
}
# Native stderr must not become a terminating error here - we want to report
# this failure in plain words, not as a red PowerShell stack.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$dockerSays = (& docker info 2>&1 | Out-String)
$dockerOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEAP

if (-not $dockerOk) {
    Say ""
    Say "  Docker is installed, but its engine is not running." "Yellow"
    Say ""
    Say "  This is a Docker problem, not an IzgoN one. Almost always it is the"
    Say "  first of these:"
    Say ""
    Say "   1. Docker Desktop has never been opened." "White"
    Say "      Start menu -> Docker Desktop -> accept the terms -> wait until it"
    Say "      says 'Engine running' at the bottom left. First start takes a"
    Say "      minute or three. Then run this setup again."
    Say ""
    Say "   2. WSL 2 needs updating." "White"
    Say "      Open Terminal as administrator and run:"
    Say "          wsl --update" "White"
    Say "          wsl --set-default-version 2" "White"
    Say "      Restart the computer, open Docker Desktop, then run this again."
    Say ""
    Say "   3. Virtualization is off in the BIOS." "White"
    Say "      Task Manager -> Performance -> CPU. If 'Virtualization' says"
    Say "      Disabled, it has to be turned on in the BIOS."
    Say ""
    Say "  What Docker actually said:" "DarkGray"
    Write-Host ("      " + ($dockerSays.Trim() -split "`n")[0]) -ForegroundColor DarkGray
    Say ""
    Read-Host "  Press Enter to close"
    exit 1
}
Say "        Docker is running." "Green"

# --- 2. Folder ---------------------------------------------------------------
$Dir = Join-Path ([Environment]::GetFolderPath("UserProfile")) "IzgoN"
Say "  [2/6] Folder: $Dir"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir

# --- 3. Compose --------------------------------------------------------------
Say "  [3/6] Writing docker-compose.yml (pulls the published image, no build)..."
$Compose = @'
services:
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes", "--save", "60", "1"]
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  izgon:
    image: ghcr.io/izgamber/izgon:latest
    pull_policy: always
    ports:
      - "8000:8000"
    environment:
      DATAPULSE_REDIS_URL: redis://redis:6379/0
      DATAPULSE_DB_PATH: /data/datapulse_events.db
      DATAPULSE_API_KEY: ${DATAPULSE_API_KEY}
      DATAPULSE_FREE_TIER_LIMIT: ${DATAPULSE_FREE_TIER_LIMIT:-10000}
      DATAPULSE_LICENSE_KEY: ${DATAPULSE_LICENSE_KEY:-}
      DATAPULSE_ALLOWED_ORIGINS: ${DATAPULSE_ALLOWED_ORIGINS:-*}
    volumes:
      - izgon-data:/data
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

volumes:
  redis-data:
  izgon-data:
'@
Set-Content -Path (Join-Path $Dir "docker-compose.yml") -Value $Compose -Encoding UTF8

# --- 4. API key --------------------------------------------------------------
$EnvPath = Join-Path $Dir ".env"
if (Test-Path $EnvPath) {
    Say "  [4/6] .env already there - keeping your existing key." "Yellow"
    $hit = Select-String -Path $EnvPath -Pattern '^DATAPULSE_API_KEY=(.*)$' | Select-Object -First 1
    if ($hit) { $Key = $hit.Matches[0].Groups[1].Value } else { $Key = "(see .env)" }
} else {
    Say "  [4/6] Generating an API key..."
    $bytes = New-Object byte[] 24
    (New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($bytes)
    $Key = [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
    Set-Content -Path $EnvPath -Encoding UTF8 -Value @(
        "# Written by IzgoN setup. Keep this file to yourself.",
        "DATAPULSE_API_KEY=$Key",
        "DATAPULSE_FREE_TIER_LIMIT=10000",
        "DATAPULSE_ALLOWED_ORIGINS=*",
        "DATAPULSE_LICENSE_KEY="
    )
}

# --- 5. Start ----------------------------------------------------------------
Say "  [5/6] Pulling the image and starting (first run takes a minute)..."
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Say ""
    Say "  docker compose failed. The output above says why." "Red"
    Read-Host "  Press Enter to close"
    exit 1
}
Say "        Waiting for the dashboard to answer..."
$up = $false
foreach ($i in 1..60) {
    Start-Sleep -Seconds 2
    try {
        if ((Invoke-WebRequest -Uri "http://localhost:8000/healthz" -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200) {
            $up = $true; break
        }
    } catch { }
}

# --- 6. Icon on the Desktop --------------------------------------------------
Say "  [6/6] Putting an IzgoN icon on your Desktop..."

# The icon file ships next to this script; copy it in so the shortcut keeps
# working after you delete the download folder.
$IcoSrc = Join-Path $Src "izgon.ico"
$Ico    = Join-Path $Dir "izgon.ico"
if (Test-Path $IcoSrc) { Copy-Item $IcoSrc $Ico -Force }

# Same for the one-click proof script, so it survives the download folder too.
foreach ($f in @("IzgoN-Test.cmd", "IzgoN-Test.ps1")) {
    $from = Join-Path $Src $f
    if (Test-Path $from) { Copy-Item $from (Join-Path $Dir $f) -Force }
}

# A launcher, so clicking the icon starts IzgoN if it is stopped and then
# opens the dashboard. Clicking it when IzgoN is already running just opens it.
$Launcher = @'
@echo off
title IzgoN
cd /d "%~dp0"
echo.
echo   Starting IzgoN...
docker compose up -d
if errorlevel 1 goto down
echo   Waiting for the dashboard...
set /a n=0
:wait
curl.exe -s -o nul -m 3 http://localhost:8000/healthz && goto open
set /a n+=1
if %n% GEQ 45 goto slow
timeout /t 2 /nobreak >nul
goto wait
:open
start "" http://localhost:8000
exit /b 0
:slow
echo.
echo   It did not answer in 90 seconds. Look at what the containers say:
echo       docker compose ps
echo       docker compose logs izgon
echo.
pause
exit /b 1
:down
echo.
echo   Docker is not running. Start Docker Desktop and click the icon again.
echo.
pause
exit /b 1
'@
$LauncherPath = Join-Path $Dir "IzgoN.cmd"
Set-Content -Path $LauncherPath -Value $Launcher -Encoding ASCII

# A second one, to stop it.
$Stop = @'
@echo off
title IzgoN - stop
cd /d "%~dp0"
docker compose down
echo.
echo   IzgoN is stopped. Your data is kept.
echo.
pause
'@
Set-Content -Path (Join-Path $Dir "IzgoN-Stop.cmd") -Value $Stop -Encoding ASCII

function New-Shortcut($LinkPath, $Target, $IconPath, $Desc) {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($LinkPath)
    $sc.TargetPath       = $Target
    $sc.WorkingDirectory = (Split-Path -Parent $Target)
    $sc.Description      = $Desc
    if (Test-Path $IconPath) { $sc.IconLocation = "$IconPath,0" }
    $sc.Save()
}

$madeIcon = $false
try {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    New-Shortcut (Join-Path $Desktop "IzgoN.lnk") $LauncherPath $Ico "IzgoN - open the delta-sync dashboard"
    $StartMenu = Join-Path ([Environment]::GetFolderPath("ApplicationData")) "Microsoft\Windows\Start Menu\Programs"
    New-Shortcut (Join-Path $StartMenu "IzgoN.lnk") $LauncherPath $Ico "IzgoN - open the delta-sync dashboard"
    $madeIcon = $true
    Say "        Icon created on the Desktop and in the Start menu." "Green"
} catch {
    Say "        Could not create the shortcut: $($_.Exception.Message)" "Yellow"
    Say "        You can still start IzgoN from $LauncherPath" "Yellow"
}

# --- done --------------------------------------------------------------------
Say ""
if ($up) {
    Say "  --------------------------------------------------" "DarkGray"
    Say "  IzgoN is running." "Green"
    Say ""
    if ($madeIcon) { Say "  Look at your Desktop - there is an IzgoN icon." "White" }
    Say "  Dashboard:  http://localhost:8000" "White"
    Say "  API key:    $Key" "White"
    Say "  Folder:     $Dir"
    $example = @"

  Want a real app window instead of a browser tab? Open the dashboard in
  Chrome or Edge, then use the install icon in the address bar (or the menu ->
  Install). It becomes a windowed app with the same icon.

  The dashboard is empty until something reports to it. To see it save
  something, double-click this, in $Dir :

      IzgoN-Test.cmd

  It sends three reports from a pretend device - one new, one identical,
  one with a single field changed - and prints what each cost on the wire
  against what it would have cost without IzgoN. Ten seconds, and the
  dashboard counters move while you watch.

  By hand instead, in PowerShell:

      `$h = @{ 'X-API-Key' = '$Key' }
      Invoke-RestMethod -Uri http://localhost:8000/api/nodes/sensor-01/sync ``
        -Method Post -ContentType 'application/json' -Headers `$h ``
        -Body '{"state": {"temp": 21.5, "hum": 60}}'

  Send it twice. The second time comes back NO_CHANGE, 0 bytes.

  Stop it:  the IzgoN-Stop shortcut, or "docker compose down" in the folder.
"@
    Write-Host $example -ForegroundColor DarkGray
    Say "  --------------------------------------------------" "DarkGray"
    Start-Process "http://localhost:8000"
} else {
    Say "  Started, but nothing answered on http://localhost:8000 in two minutes." "Yellow"
    Say "  Check what the containers say:" "Yellow"
    Say ""
    Say "      docker compose ps" "White"
    Say "      docker compose logs izgon" "White"
    Say ""
    Say "  Folder: $Dir"
}

Say ""
Read-Host "  Press Enter to close"
