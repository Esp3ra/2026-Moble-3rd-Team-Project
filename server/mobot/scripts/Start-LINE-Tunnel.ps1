[CmdletBinding(DefaultParameterSetName='Connect')]
param (
    [Parameter(ParameterSetName='Connect', Mandatory)]
    [ValidatePattern('^[a-zA-Z0-9_][a-zA-Z0-9_.-]*@[a-zA-Z0-9][a-zA-Z0-9_.-]*$')]
    [string] $Pi,

    [Parameter(ParameterSetName='Check', Mandatory)]
    [switch] $CheckOnly,

    [string] $PiConfigPath = (Join-Path $env:LOCALAPPDATA 'MOBOT/line-pi-config.json')
)

$ErrorActionPreference = 'Stop'
$mobotInstance = if ($env:MOBOT_DB) {
    Split-Path -Parent ([IO.Path]::GetFullPath($env:MOBOT_DB))
} else {
    Join-Path $env:LOCALAPPDATA 'MOBOT/instance'
}
$settings = Get-Content -LiteralPath (Join-Path $mobotInstance 'factory-v1.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$line = $settings.devices.LINE
if (-not $line.base_url -or -not $line.controller_token -or -not $settings.device_tokens.LINE) {
    throw 'Register the LINE settings with configure_live_line.py first.'
}
$lineAddress = [uri]$line.base_url
if (-not $lineAddress.IsAbsoluteUri -or $lineAddress.Scheme -ne 'http' -or $lineAddress.Host -ne '127.0.0.1' -or $lineAddress.AbsolutePath -ne '/' -or $lineAddress.Query -or $lineAddress.Fragment -or $lineAddress.UserInfo) {
    throw 'This launcher requires a loopback HTTP base URL for LINE.'
}

if ($CheckOnly) {
    $headers = @{ Authorization = "Bearer $($line.controller_token)" }
    $status = Invoke-RestMethod -Uri ([uri]::new($lineAddress,'/device/v1/status')) -Headers $headers -TimeoutSec 5
    if ($status.device_id -ne 'LINE') { throw 'The endpoint did not reply as LINE.' }
    [pscustomobject]@{ PiApi = 'Connected'; UnoOnline = $status.online; LineState = $status.state }
    return
}

$piConfig = Get-Content -LiteralPath $PiConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$eventAddress = [uri]$piConfig.server_url
if ($piConfig.listen_host -ne '127.0.0.1' -or -not $eventAddress.IsAbsoluteUri -or $eventAddress.Scheme -ne 'http' -or $eventAddress.Host -ne '127.0.0.1' -or $eventAddress.AbsolutePath -ne '/' -or $eventAddress.UserInfo -or $eventAddress.Query -or $eventAddress.Fragment) {
    throw 'Pi listen_host and server_url must point to loopback for this deployment.'
}
if ($piConfig.controller_token -cne $line.controller_token -or $piConfig.device_token -cne $settings.device_tokens.LINE) {
    throw 'The Pi configuration keys differ from the server settings. Register the matching Pi config.'
}
$piPort = [int]$piConfig.listen_port
$pcPort = if ($env:MOBOT_PORT) { [int]$env:MOBOT_PORT } else { 8000 }
if ($piPort -lt 1 -or $piPort -gt 65535 -or $pcPort -lt 1 -or $pcPort -gt 65535) {
    throw 'Invalid Pi or main server port.'
}
$localForward = '127.0.0.1:{0}:127.0.0.1:{1}' -f $lineAddress.Port,$piPort
$remoteForward = '127.0.0.1:{0}:127.0.0.1:{1}' -f $eventAddress.Port,$pcPort
& ssh -N -T -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -L $localForward -R $remoteForward $Pi
if ($LASTEXITCODE -ne 0) { throw "SSH tunnel stopped with exit code $LASTEXITCODE." }
