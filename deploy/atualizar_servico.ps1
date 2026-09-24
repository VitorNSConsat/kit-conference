<#
.SYNOPSIS
  Atualiza a Conferencia de Kits no servidor: git pull, dependencias e reinicio.
  Rode como Administrador, de qualquer pasta:
      powershell -ExecutionPolicy Bypass -File .\deploy\atualizar_servico.ps1
  O banco (kit_conference.db) e o .env nao sao tocados.
#>
param([string]$Nome = "KitConference")

$ErrorActionPreference = "Stop"
$eAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $eAdmin) { Write-Host "Abra o PowerShell como Administrador." -ForegroundColor Red; exit 1 }

$app = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $app

Write-Host "1/4 git pull..."
git pull
if ($LASTEXITCODE -ne 0) { Write-Host "git pull falhou - resolva antes de continuar." -ForegroundColor Red; exit 1 }

$venv = Join-Path $app ".venv\Scripts\python.exe"
$py = if (Test-Path $venv) { $venv } else { (Get-Command python).Source }
Write-Host "2/4 dependencias..."
& $py -m pip install -q -r requirements.txt

Write-Host "3/4 reiniciando o servico $Nome..."
Restart-Service -Name $Nome

Write-Host "4/4 conferindo..."
Start-Sleep -Seconds 5
$porta = 8080
$linha = Select-String -Path (Join-Path $app ".env") -Pattern '^\s*PORTA\s*=\s*(\d+)' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($linha) { $porta = [int]$linha.Matches[0].Groups[1].Value }
try {
    $r = Invoke-WebRequest -Uri "http://localhost:$porta/ping" -UseBasicParsing -TimeoutSec 10
    Write-Host "OK - respondeu $($r.StatusCode) em http://localhost:$porta" -ForegroundColor Green
} catch {
    Write-Host "O servico reiniciou mas nao respondeu em /ping. Veja servico.log na pasta do projeto." -ForegroundColor Red
    exit 1
}
