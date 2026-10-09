param([Parameter(Mandatory=$true)][ValidateSet('quickstart','deploy')][string]$Mode)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install Docker Desktop with Compose v2 first.' }
function Invoke-Compose {
    & docker compose @script:ComposeArguments @args
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose failed. Check the service logs.' }
}
if ($Mode -eq 'quickstart') {
    $script:ComposeArguments = @('-f', (Join-Path $PSScriptRoot 'deploy/quickstart/compose.yaml'))
    Invoke-Compose up -d --build --wait --wait-timeout 300
    $port = if ($env:NEXUSDESK_PORT) { $env:NEXUSDESK_PORT } else { '8080' }
    Write-Host "NexusDesk demo: https://localhost:$port"
    Write-Host 'Self-signed certificate; import the generated ca.crt as a trusted root to silence the browser warning.'
} else {
    $environmentFile = Join-Path $PSScriptRoot 'deploy/production/.env'
    if (-not (Test-Path -LiteralPath $environmentFile)) { throw 'Copy deploy/production/.env.example to .env and configure credentials first.' }
    $script:ComposeArguments = @('--env-file', $environmentFile, '-f', (Join-Path $PSScriptRoot 'deploy/production/compose.yaml'))
    Invoke-Compose build
    Invoke-Compose stop web api runtime-worker knowledge-worker
    Invoke-Compose up --no-deps --force-recreate --exit-code-from migrate migrate
    Invoke-Compose up -d --wait --wait-timeout 180
    Write-Host 'NexusDesk services started. Open the configured web port.'
}
