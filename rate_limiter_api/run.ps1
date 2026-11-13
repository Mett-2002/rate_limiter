param([int]$Port = 8200)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& '.\.venv\Scripts\python.exe' manage.py runserver "127.0.0.1:$Port"
