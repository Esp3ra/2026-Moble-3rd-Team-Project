[CmdletBinding()]
param(
    [switch] $CheckOnly,
    [string] $Settings,
    [ValidateRange(1, 7)] [int] $ConfirmedPosition
)
$ErrorActionPreference = 'Stop'
$python = if ($env:MOBOT_PYTHON) { $env:MOBOT_PYTHON } else {
    Join-Path $env:LOCALAPPDATA 'MOBOT\.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $python)) { throw 'Install the MOBOT Python environment first.' }
$arguments = @('-B', (Join-Path $PSScriptRoot 'run_live_agv.py'))
if ($Settings) { $arguments += @('--settings', $Settings) }
if ($CheckOnly) { $arguments += '--check-only' }
if ($PSBoundParameters.ContainsKey('ConfirmedPosition')) {
    $arguments += @('--confirmed-position', [string]$ConfirmedPosition)
}
& $python @arguments
exit $LASTEXITCODE
