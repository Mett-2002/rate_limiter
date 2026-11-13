param([int]$Port = 8300)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $env:DEMO_API_KEY -and -not (Test-Path -LiteralPath '.runtime\credentials.json')) {
    throw 'Run scripts/prepare_demo.py in the API project first, or set DEMO_API_KEY.'
}
& '.\.venv\Scripts\python.exe' manage.py runserver "127.0.0.1:$Port"
