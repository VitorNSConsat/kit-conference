<#
.SYNOPSIS
  Instala Conferencia de Kits como SERVICO do Windows (unica forma de execucao suportada).

.DESCRIPTION
  Windows -> Servico KitConference (NSSM) -> python.exe run.py -> aplicacao.

  - Inicia sozinho com o Windows, sem ninguem logado (conta LocalSystem).
  - Reinicia sozinho se o processo cair.
  - Grava o log em servico.log (com rotacao).
  - Nao usa Tarefa Agendada: a tarefa antiga "KitConference", se existir, e removida
    (ela so servia para iniciar este app), e qualquer outra tarefa que aponte
    para esta pasta e DESATIVADA (e listada) -- nada mais e apagado.

  Rode num PowerShell COMO ADMINISTRADOR, de qualquer pasta:

      powershell -ExecutionPolicy Bypass -File .\deploy\instalar_servico.ps1

  Rodar de novo reinstala o servico. Para conferir depois:

      powershell -ExecutionPolicy Bypass -File .\deploy\validar_servico.ps1

.PARAMETER Python
  Caminho do python.exe. Padrao: .venv\Scripts\python.exe do projeto, se existir;
  senao o python do PATH.
#>
param([string]$Python = "")

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$servico = "KitConference"
$tarefaAntiga = "KitConference"
$app = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logs = Join-Path $app "."

# --- python ----------------------------------------------------------------
if (-not $Python) {
    $venv = Join-Path $app ".venv\Scripts\python.exe"
    if (Test-Path $venv) { $Python = $venv }
    else { $c = Get-Command python -ErrorAction SilentlyContinue; if ($c) { $Python = $c.Source } }
}
if (-not $Python -or -not (Test-Path $Python)) {
    Write-Host "python.exe nao encontrado. Informe com -Python 'C:\caminho\python.exe'." -ForegroundColor Red
    exit 1
}
Write-Host "Python : $Python"
Write-Host "Pasta  : $app"
if (-not (Test-Path (Join-Path $app "run.py"))) {
    Write-Host "run.py nao encontrado em $app" -ForegroundColor Red; exit 1
}
if (Test-Path (Join-Path $app "requirements.txt")) {
    & $Python -c "import fastapi, uvicorn" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Instalando dependencias (requirements.txt)..."
        & $Python -m pip install -r (Join-Path $app "requirements.txt")
        if ($LASTEXITCODE -ne 0) { Write-Host "Falha ao instalar dependencias." -ForegroundColor Red; exit 1 }
    }
}

# --- NSSM: garante um nssm.exe em pasta da MAQUINA (nao do perfil de um usuario)
function Achar-Nssm {
    $fixo = Join-Path $env:ProgramFiles "nssm\nssm.exe"
    if (Test-Path $fixo) { return $fixo }
    $cand = @((Join-Path $PSScriptRoot "nssm.exe"), "C:\nssm\nssm.exe", "C:\nssm\win64\nssm.exe")
    foreach ($p in $cand) { if (Test-Path $p) { return $p } }
    $c = Get-Command nssm -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $pasta = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $pasta) {
        $f = Get-ChildItem $pasta -Recurse -Filter nssm.exe -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -match "win64" } | Select-Object -First 1
        if ($f) { return $f.FullName }
    }
    return $null
}
$nssm = Achar-Nssm
if (-not $nssm) {
    Write-Host "NSSM nao encontrado; instalando via winget..."
    winget install --id NSSM.NSSM -e --accept-package-agreements --accept-source-agreements | Out-Null
    $nssm = Achar-Nssm
}
if (-not $nssm) {
    Write-Host "Nao consegui obter o NSSM. Baixe em https://nssm.cc/download e coloque o nssm.exe em C:\nssm\ ou em deploy\." -ForegroundColor Red
    exit 1
}
$fixo = Join-Path $env:ProgramFiles "nssm\nssm.exe"
if ($nssm -ne $fixo) {
    New-Item -ItemType Directory -Force -Path (Split-Path $fixo) | Out-Null
    Copy-Item $nssm $fixo -Force
    $nssm = $fixo
}
Write-Host "NSSM   : $nssm"

# --- Tarefa Agendada: sai de cena -------------------------------------------
$tarefa = Get-ScheduledTask -TaskName $tarefaAntiga -ErrorAction SilentlyContinue
if ($tarefa) {
    Write-Host "Removendo a tarefa agendada '$tarefaAntiga' (o servico assume)..."
    Stop-ScheduledTask -TaskName $tarefaAntiga -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $tarefaAntiga -Confirm:$false
}
foreach ($t in Get-ScheduledTask -ErrorAction SilentlyContinue) {
    foreach ($a in $t.Actions) {
        $linha = "$($a.Execute) $($a.Arguments) $($a.WorkingDirectory)"
        if ($t.TaskName -ne $tarefaAntiga -and $linha -like "*$app*" -and $t.State -ne "Disabled") {
            Write-Host "Tarefa '$($t.TaskName)' aponta para esta pasta: DESATIVADA (nao apagada)." -ForegroundColor Yellow
            Disable-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Out-Null
            break
        }
    }
}

# --- (re)instala o servico --------------------------------------------------
# Reinstalar nao pode perder a PORTAL_SERVICE_KEY que o servico atual ja tem.
$chaveAntiga = $null
if (Get-Service -Name $servico -ErrorAction SilentlyContinue) {
    $extra = ((& $nssm get $servico AppEnvironmentExtra) -join " ") -replace "`0", ""
    if ($extra -match "PORTAL_SERVICE_KEY=(\S+)") { $chaveAntiga = $Matches[1] }
}
if (Get-Service -Name $servico -ErrorAction SilentlyContinue) {
    Write-Host "Servico $servico ja existe: removendo para reinstalar..."
    try { & $nssm stop $servico *> $null } catch {}
    try { & $nssm remove $servico confirm *> $null } catch {}
}
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs "servico.log"

& $nssm install $servico $Python "run.py" | Out-Null
& $nssm set $servico AppDirectory $app | Out-Null
& $nssm set $servico DisplayName "Conferencia de Kits" | Out-Null
& $nssm set $servico Description "Conferencia de Kits - http://localhost:8080" | Out-Null
& $nssm set $servico Start SERVICE_AUTO_START | Out-Null
& $nssm set $servico ObjectName LocalSystem | Out-Null
& $nssm set $servico AppStdout $log | Out-Null
& $nssm set $servico AppStderr $log | Out-Null
& $nssm set $servico AppRotateFiles 1 | Out-Null
& $nssm set $servico AppRotateBytes 5242880 | Out-Null
& $nssm set $servico AppExit Default Restart | Out-Null
& $nssm set $servico AppRestartDelay 5000 | Out-Null

# Variaveis de ambiente do servico (o ambiente do usuario nao chega no LocalSystem).
$env_ = @()
$chave = if ($env:PORTAL_SERVICE_KEY) { $env:PORTAL_SERVICE_KEY } elseif ($chaveAntiga) { $chaveAntiga } else { [Environment]::GetEnvironmentVariable("PORTAL_SERVICE_KEY", "Machine") }
if ($chave) { $env_ += "PORTAL_SERVICE_KEY=$chave" }
else { Write-Host "Aviso: PORTAL_SERVICE_KEY nao definida - o painel Pessoas do Portal nao gerencia usuarios aqui." -ForegroundColor Yellow }
if ($env_.Count -gt 0) { & $nssm set $servico AppEnvironmentExtra $env_ | Out-Null }

# --- firewall ---------------------------------------------------------------
$regra = "Conferencia de Kits 8080"
if (-not (Get-NetFirewallRule -DisplayName $regra -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $regra -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080,8011 | Out-Null
    Write-Host "Regra de firewall criada para a(s) porta(s) 8080,8011."
}

& $nssm start $servico | Out-Null
Start-Sleep -Seconds 5
Write-Host ""
Write-Host "Servico $servico : $((Get-Service -Name $servico).Status)" -ForegroundColor Green
Write-Host "Acesse: http://localhost:8080"
Write-Host "Log   : $log"
Write-Host "Conferir tudo: powershell -ExecutionPolicy Bypass -File .\deploy\validar_servico.ps1"
