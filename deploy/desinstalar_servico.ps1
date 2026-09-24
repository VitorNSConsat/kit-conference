<#
.SYNOPSIS
  Remove o servico KitConference (nao apaga arquivos nem o banco). Rode como Administrador:
      powershell -ExecutionPolicy Bypass -File .\deploy\desinstalar_servico.ps1
#>
#Requires -RunAsAdministrator
$servico = "KitConference"
$nssm = @((Join-Path $env:ProgramFiles "nssm\nssm.exe"), (Join-Path $PSScriptRoot "nssm.exe"), "C:\nssm\nssm.exe", "C:\nssm\win64\nssm.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $nssm) { $c = Get-Command nssm -ErrorAction SilentlyContinue; if ($c) { $nssm = $c.Source } }
if (-not $nssm) { Write-Host "nssm.exe nao encontrado." -ForegroundColor Red; exit 1 }
if (Get-Service -Name $servico -ErrorAction SilentlyContinue) {
    try { & $nssm stop $servico *> $null } catch {}
    & $nssm remove $servico confirm
    Write-Host "Servico $servico removido."
} else {
    Write-Host "Servico $servico nao existe."
}
