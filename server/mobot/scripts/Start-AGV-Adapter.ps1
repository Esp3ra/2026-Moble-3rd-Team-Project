[CmdletBinding(DefaultParameterSetName='Run')]
param (
    [Parameter(ParameterSetName='Check', Mandatory)]
    [switch] $CheckOnly,

    [Parameter(ParameterSetName='Run')]
    [string] $AgvUrl = "http://192.168.137.144",

    [Parameter(ParameterSetName='Run')]
    [string] $DeviceKey = "60abfb0b7cec9d622e8bae967950a14ac441c01386f6a9612aa88282dca96a5c",

    [Parameter(ParameterSetName='Run')]
    [string] $ServerEventsUrl = "http://127.0.0.1:8000/device/v1/events",

    [Parameter(ParameterSetName='Run')]
    [string] $JournalPath = (Join-Path $env:LOCALAPPDATA 'MOBOT/agv_journal.sqlite3')
)

$ErrorActionPreference = 'Stop'
$mobotInstance = if ($env:MOBOT_DB) {
    Split-Path -Parent ([IO.Path]::GetFullPath($env:MOBOT_DB))
} else {
    Join-Path $env:LOCALAPPDATA 'MOBOT/instance'
}

$settingsPath = Join-Path $mobotInstance 'factory-v1.json'
if (-not (Test-Path $settingsPath)) {
    throw "factory-v1.json not found at $settingsPath. Run configure_live_agv.py first."
}

$settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$agv = $settings.devices.AGV
if (-not $agv.base_url -or -not $agv.controller_token -or -not $settings.device_tokens.AGV) {
    throw 'Register the AGV settings in factory-v1.json first.'
}

$agvAddress = [uri]$agv.base_url

if ($CheckOnly) {
    $headers = @{ Authorization = "Bearer $($agv.controller_token)" }
    try {
        $status = Invoke-RestMethod -Uri ([uri]::new($agvAddress, '/device/v1/status')) -Headers $headers -TimeoutSec 5
        if ($status.device_id -ne 'AGV') { throw 'The endpoint did not reply as AGV.' }
        [pscustomobject]@{
            AdapterApi = 'Connected'
            Online     = $status.online
            State      = $status.state
            Position   = $status.position
            BootId     = $status.boot_id
        }
    } catch {
        Write-Error "AGV adapter check failed at $($agvAddress): $_"
    }
    return
}

# Run AGV device adapter with registered tokens
$env:AGV_URL = $AgvUrl
$env:AGV_DEVICE_KEY = $DeviceKey
$env:AGV_CONTROLLER_TOKEN = [string]$agv.controller_token
$env:AGV_EVENT_TOKEN = [string]$settings.device_tokens.AGV
$env:AGV_EVENTS_URL = $ServerEventsUrl

$timeout = if ($agv.request_timeout_seconds) { [float]$agv.request_timeout_seconds } else { 15.0 }
$port = $agvAddress.Port

Write-Host "Starting AGV device adapter on port $port (timeout: ${timeout}s)..." -ForegroundColor Cyan
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir '..\..\..')).Path
Set-Location $repoRoot

python AGV/device_adapter.py `
    --journal $JournalPath `
    --listen $agvAddress.Host `
    --port $port `
    --command-timeout-seconds $timeout `
    --poll-seconds 0.5 `
    --heartbeat-seconds 0.3
