[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker is not installed. Complete the administrator prerequisites in docs/Hummingbot first.'
}
$engine = & docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $engine -ne 'linux') { throw 'Start the Docker Linux engine first.' }
$composePath = Join-Path $PSScriptRoot 'compose.json'
& docker compose -f $composePath config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed.' }
& docker compose -f $composePath pull
if ($LASTEXITCODE -ne 0) { throw 'Image pull failed.' }
& docker compose -f $composePath up -d
if ($LASTEXITCODE -ne 0) { throw 'Container startup failed.' }
& docker compose -f $composePath exec -T hummingbot hbot --version
if ($LASTEXITCODE -ne 0) { throw 'Hummingbot CLI verification failed.' }
Write-Output 'Container ready. Create the paper connector config using the documented hbot commands.'
