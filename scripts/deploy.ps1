# Deploy the integration to Home Assistant over SSH and restart core.
# Requires an SSH config host "homeassistant" (Advanced SSH & Web Terminal).
#
#   .\scripts\deploy.ps1
#   .\scripts\deploy.ps1 -NoRestart
param(
    [string]$Target = "homeassistant",
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$src = Join-Path $repoRoot "custom_components\shelly_firmware"
$remote = "/config/custom_components/shelly_firmware"
$tar = Join-Path $env:TEMP "shelly_firmware_deploy.tar"

if (Test-Path $tar) { Remove-Item $tar -Force }
# bsdtar stores the directory name shelly_firmware at the archive root.
tar -cf $tar -C (Join-Path $repoRoot "custom_components") shelly_firmware
if ($LASTEXITCODE -ne 0) { throw "tar failed" }

Write-Host "Copying to $Target..."
python -c @"
import subprocess, sys
data = open(r'$tar', 'rb').read()
cmd = [
    'ssh', '-o', 'BatchMode=yes', '$Target',
    'sudo mkdir -p /config/custom_components && sudo rm -rf $remote && sudo tar -xf - -C /config/custom_components && test -f $remote/manifest.json'
]
raise SystemExit(subprocess.run(cmd, input=data).returncode)
"@
if ($LASTEXITCODE -ne 0) { throw "remote extract failed" }

if ($NoRestart) {
    Write-Host "Deployed (restart skipped)."
} else {
    Write-Host "Restarting Home Assistant core..."
    Start-Sleep -Seconds 4
    ssh -o BatchMode=yes $Target "bash -lc 'ha core restart'"
    if ($LASTEXITCODE -ne 0) { throw "ha core restart failed" }
    Write-Host "Deployed and restarted."
}
