# IzgoN - proof that it works, in one click.
#
# Double-click IzgoN-Test.cmd. This is the script it runs.
#
# It sends three reports from a pretend device and prints what each one cost
# on the wire:
#
#   1. a first report          -> the server has nothing, so it takes the lot
#   2. the exact same report   -> NO_CHANGE, 0 bytes on the wire
#   3. one field changed       -> only that field travels
#
# Nothing here is simulated. The numbers printed at the end are the numbers the
# server measured and wrote to its own event log.

$ErrorActionPreference = "Stop"
function Say([string]$Text, [string]$Colour = "Gray") { Write-Host $Text -ForegroundColor $Colour }

$Dir = Join-Path ([Environment]::GetFolderPath("UserProfile")) "IzgoN"
$Base = "http://localhost:8000"
# A fresh node id per run, so the first report is always genuinely the first
# one the server has seen for it. Re-using one id would make the second run
# start from the state the first run left behind, and the numbers below would
# tell a story about the wrong thing.
$Node = "demo-" + (Get-Date -Format "HHmmss")

Say ""
Say "  IzgoN - does it actually save anything?" "Cyan"
Say "  --------------------------------------------------" "DarkGray"
Say ""

# --- the folder has to exist ---------------------------------------------
if (-not (Test-Path (Join-Path $Dir "docker-compose.yml"))) {
    Say "  $Dir is not there (or has no docker-compose.yml)." "Yellow"
    Say "  Run IzgoN-Setup.cmd first, then this." "Yellow"
    Say ""
    Read-Host "  Press Enter to close"
    exit 1
}
Set-Location $Dir

# --- is it answering? if not, start it -----------------------------------
function Test-Izgon {
    try { return (Invoke-WebRequest -Uri "$Base/healthz" -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200 }
    catch { return $false }
}

Say "  [1/5] Is IzgoN running?"
if (Test-Izgon) {
    Say "        Yes." "Green"
} else {
    Say "        Not yet - starting it." "Yellow"

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker info 2>&1 | Out-Null
    $dockerOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP

    if (-not $dockerOk) {
        Say ""
        Say "  Docker is not running, so IzgoN cannot start." "Yellow"
        Say "  Open Docker Desktop, wait until it says 'Engine running' at the"
        Say "  bottom left, then run this again."
        Say ""
        Read-Host "  Press Enter to close"
        exit 1
    }

    docker compose up -d | Out-Null
    $ok = $false
    foreach ($i in 1..45) {
        Start-Sleep -Seconds 2
        if (Test-Izgon) { $ok = $true; break }
    }
    if (-not $ok) {
        Say ""
        Say "  It did not answer on $Base in 90 seconds." "Yellow"
        Say "  Look at what the containers say:"
        Say "      docker compose ps" "White"
        Say "      docker compose logs izgon" "White"
        Say ""
        Read-Host "  Press Enter to close"
        exit 1
    }
    Say "        Started." "Green"
}

# --- the key, read out of .env so nothing has to be copied ----------------
Say "  [2/5] Reading your API key from .env..."
$EnvPath = Join-Path $Dir ".env"
$hit = Select-String -Path $EnvPath -Pattern '^DATAPULSE_API_KEY=(.+)$' | Select-Object -First 1
if (-not $hit) {
    Say "        No DATAPULSE_API_KEY in $EnvPath." "Yellow"
    Read-Host "  Press Enter to close"
    exit 1
}
$Key = $hit.Matches[0].Groups[1].Value.Trim()
Say "        Found it (not printing it - it is a secret)." "Green"
$Headers = @{ "X-API-Key" = $Key }

# The wire statuses are short and a bit technical. SYNC_REQUIRED in particular
# reads like a warning when it is in fact the good case - the delta. Say it in
# words instead.
function Plain([string]$Status) {
    switch ($Status) {
        "FULL_STATE"    { return "took everything" }
        "NO_CHANGE"     { return "sent nothing" }
        "SYNC_REQUIRED" { return "sent only the change" }
        default         { return $Status }
    }
}

function Send-Report([string]$Label, [string]$Body) {
    $r = Invoke-RestMethod -Uri "$Base/api/nodes/$Node/sync" -Method Post `
        -ContentType "application/json" -Headers $Headers -Body $Body -TimeoutSec 15
    $d = ""
    if ($r.delta) { $d = ($r.delta | ConvertTo-Json -Compress) }
    return [pscustomobject]@{
        Label = $Label
        Status = (Plain $r.status)
        Sent = [int]$r.bytes_sent
        Full = [int]$r.bytes_full
        Delta = $d
    }
}

# A report that looks like something a real device sends: mostly fixed fields,
# one or two that move. Built as an object rather than a hand-written string so
# the node id lands in it correctly and the JSON cannot end up malformed.
function New-Report([double]$Temp) {
    $state = [ordered]@{
        device   = $Node
        firmware = "2.4.1"
        site     = "warehouse-north"
        battery  = 97
        signal   = -71
        temp     = $Temp
        hum      = 60
        uptime_s = 86400
        errors   = 0
    }
    return (@{ state = $state } | ConvertTo-Json -Compress -Depth 5)
}
$reportA = New-Report 21.5
$reportB = New-Report 21.9

$results = @()

Say "  [3/5] First report from $Node..."
$r1 = Send-Report "1. first report" $reportA
$results += $r1
Say ("        {0} - {1} bytes on the wire" -f $r1.Status, $r1.Sent) "White"

Say "  [4/5] The exact same report again..."
$r2 = Send-Report "2. identical report" $reportA
$results += $r2
Say ("        {0} - {1} bytes on the wire" -f $r2.Status, $r2.Sent) "Green"

Say "  [5/5] Same report with the temperature changed 21.5 -> 21.9..."
$r3 = Send-Report "3. one field changed" $reportB
$results += $r3
Say ("        {0} - {1} bytes on the wire" -f $r3.Status, $r3.Sent) "Green"
if ($r3.Delta) { Say ("        what travelled: {0}" -f $r3.Delta) "White" }

# --- the sum ---------------------------------------------------------------
$sent = ($results | Measure-Object -Property Sent -Sum).Sum
$full = ($results | Measure-Object -Property Full -Sum).Sum
$saved = $full - $sent
$pct = if ($full -gt 0) { [math]::Round(100.0 * $saved / $full, 1) } else { 0 }

Say ""
Say "  --------------------------------------------------" "DarkGray"
Say ("  {0,-22} {1,-22} {2,9} {3,10}" -f "", "what the server did", "sent", "if naive") "DarkGray"
foreach ($r in $results) {
    Say ("  {0,-22} {1,-22} {2,7} B {3,8} B" -f $r.Label, $r.Status, $r.Sent, $r.Full)
}
Say "  --------------------------------------------------" "DarkGray"
Say ("  Three reports cost {0} bytes." -f $sent) "White"
Say ("  Sending the full state every time: {0} bytes." -f $full) "White"
Say ("  Saved: {0} bytes - {1}% of the traffic." -f $saved, $pct) "Green"
Say ""
Say "  That is the whole product. A device that reports every 30 seconds and" "DarkGray"
Say "  changes rarely saves roughly that share of its data bill, every day." "DarkGray"
Say ""
Say "  The dashboard is opening - the counters now show these same numbers," "DarkGray"
Say "  read from the server's own event log." "DarkGray"
Say ""
Start-Process $Base
Read-Host "  Press Enter to close"
