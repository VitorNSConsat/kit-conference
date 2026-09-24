<#
.SYNOPSIS
  Confere que Conferencia de Kits roda SO como servico do Windows. Como Administrador:
      powershell -ExecutionPolicy Bypass -File .\deploy\validar_servico.ps1
      powershell -ExecutionPolicy Bypass -File .\deploy\validar_servico.ps1 -Ciclo   (tambem para/inicia/reinicia)

  Verifica: servico existe, inicio automatico, roda como LocalSystem, usa o
  python e a pasta certos, nao ha Tarefa Agendada ativa apontando para o app,
  a porta responde e o log existe. Com -Ciclo, testa parar, iniciar e reiniciar.
  O teste de reinicio do Windows e manual (ultima linha do resultado).
#>
param([switch]$Ciclo)

#Requires -RunAsAdministrator
$servico = "KitConference"
$app = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$porta = 8080
$falhas = 0
function Item($ok, $texto) {
    if ($ok) { Write-Host "[ OK ] $texto" -ForegroundColor Green }
    else { Write-Host "[FALHA] $texto" -ForegroundColor Red; $script:falhas++ }
}
function Responde {
    try { $r = Invoke-WebRequest -Uri "http://localhost:$porta/" -UseBasicParsing -TimeoutSec 8 -MaximumRedirection 3; return $true }
    catch { if ($_.Exception.Response) { return $true } else { return $false } }
}
function Espera-Responder($segundos) {
    $fim = (Get-Date).AddSeconds($segundos)
    while ((Get-Date) -lt $fim) { if (Responde) { return $true }; Start-Sleep -Seconds 2 }
    return $false
}

$svc = Get-CimInstance Win32_Service -Filter "Name='$servico'" -ErrorAction SilentlyContinue
Item ($null -ne $svc) "Servico '$servico' existe"
if ($svc) {
    Item ($svc.StartMode -eq "Auto") "Inicio automatico com o Windows (StartMode=$($svc.StartMode))"
    Item ($svc.StartName -eq "LocalSystem") "Roda sem depender de login (conta $($svc.StartName))"
    Item ($svc.State -eq "Running") "Servico em execucao (State=$($svc.State))"
    $nssm = (($svc.PathName -replace '"', '') -split "\s+" | Select-Object -First 1)
    if (Test-Path $nssm) {
        $exe = (& $nssm get $servico Application) -join "" -replace "`0", ""
        $dir = (& $nssm get $servico AppDirectory) -join "" -replace "`0", ""
        $par = (& $nssm get $servico AppParameters) -join "" -replace "`0", ""
        Item (Test-Path $exe) "Python do servico: $exe"
        Item ($dir -eq $app) "Diretorio de trabalho: $dir"
        Item ($par -like "*run.py*") "Comando: $par"
        $saida = (& $nssm get $servico AppStdout) -join "" -replace "`0", ""
        Item ($saida -ne "") "Log configurado: $saida"
    }
}

$ativas = @()
foreach ($t in Get-ScheduledTask -ErrorAction SilentlyContinue) {
    foreach ($a in $t.Actions) {
        if (("$($a.Execute) $($a.Arguments) $($a.WorkingDirectory)" -like "*$app*") -and $t.State -ne "Disabled") { $ativas += $t.TaskName; break }
    }
}
Item (($ativas.Count -eq 0) -and -not (Get-ScheduledTask -TaskName "KitConference" -ErrorAction SilentlyContinue)) "Nenhuma Tarefa Agendada inicia o app$(if ($ativas) { ' (' + ($ativas -join ', ') + ')' })"
Item (Responde) "Aplicacao responde em http://localhost:$porta"

if ($Ciclo -and $svc) {
    Write-Host "--- ciclo de servico ---"
    Stop-Service $servico; Start-Sleep -Seconds 3
    Item ((Get-Service $servico).Status -eq "Stopped") "Parar: servico parou"
    Item (-not (Responde)) "Parar: aplicacao deixou de responder"
    Start-Service $servico
    Item (Espera-Responder 40) "Iniciar: aplicacao voltou"
    Restart-Service $servico
    Item (Espera-Responder 40) "Reiniciar: aplicacao voltou"
}

Write-Host ""
if ($falhas -eq 0) { Write-Host "Tudo certo." -ForegroundColor Green } else { Write-Host "$falhas item(ns) com falha - veja o log em .\servico.log." -ForegroundColor Red }
Write-Host "Teste do boot (manual): reinicie o Windows SEM fazer login e, de outro computador, abra http://IP-DO-SERVIDOR:$porta."
exit $falhas
