param([Parameter(Mandatory=$true)][string]$Directory,[switch]$ConfirmedStandbyAt2,[switch]$RecoverIdleStandby,[string]$ReconcileCompletedDispense)
$ErrorActionPreference = 'Stop'
$trialRoot = (Resolve-Path -LiteralPath $Directory).Path
$trialSettings = Join-Path $trialRoot 'factory-v1.json'
$trialProfile = Get-Content -LiteralPath $trialSettings -Raw | ConvertFrom-Json
if ($trialProfile.mixed_trial.profile -ne 'LINE_AGV_MIXED_V1') { throw 'Not a mixed trial profile.' }
$trialPython = if ($env:MOBOT_PYTHON) { $env:MOBOT_PYTHON } else { Join-Path $env:LOCALAPPDATA 'MOBOT\.venv\Scripts\python.exe' }
$env:MOBOT_DB = Join-Path $trialRoot 'mobot.sqlite3'
$env:MOBOT_MIXED_TRIAL = '1'
$env:MOBOT_V1_MODE = 'HARDWARE'
$env:MOBOT_V1_BACKGROUND = '1'
$env:MOBOT_HOST = '127.0.0.1'
$env:MOBOT_PORT = '8000'
# Keep startup and child shutdown in one process; don't launch orphaned controls.
$trialArguments = @('-B',(Join-Path $PSScriptRoot 'run_mixed_trial.py'),'--settings',$trialSettings)
if ($ConfirmedStandbyAt2) { $trialArguments += @('--confirmed-position','2') }
if ($RecoverIdleStandby) { $trialArguments += '--recover-idle-standby' }
if ($ReconcileCompletedDispense) { $trialArguments += @('--reconcile-completed-dispense',$ReconcileCompletedDispense) }
& $trialPython @trialArguments
exit $LASTEXITCODE
