<#
.SYNOPSIS
  Remove o servico da Conferencia de Kits (nao apaga arquivos nem o banco).
  Rode como Administrador:
      powershell -ExecutionPolicy Bypass -File .\deploy\remover_servico.ps1
#>
param([string]$Nome = "KitConference")

$eAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $eAdmin) { Write-Host "Abra o PowerShell como Administrador." -ForegroundColor Red; exit 1 }

$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
if (-not $nssm) {
    $f = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") -Recurse -Filter nssm.exe -ErrorAction SilentlyContinue |
         Where-Object { $_.FullName -match "win64" } | Select-Object -First 1
    if ($f) { $nssm = $f.FullName }
}
if (-not $nssm) { Write-Host "nssm.exe nao encontrado." -ForegroundColor Red; exit 1 }

if (Get-Service -Name $Nome -ErrorAction SilentlyContinue) {
    & $nssm stop $Nome | Out-Null
    & $nssm remove $Nome confirm | Out-Null
    Write-Host "Servico $Nome removido."
} else {
    Write-Host "Servico $Nome nao existe."
}
