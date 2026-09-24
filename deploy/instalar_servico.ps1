<#
.SYNOPSIS
  Instala a Conferencia de Kits como servico do Windows (sobe com a maquina).

.DESCRIPTION
  Usa o NSSM para rodar "python run.py" como servico: UMA porta, 8080 (HTTP),
  para notebooks e celulares (o HTTPS da nuvem vem do Cloudflare Tunnel). Se
  existir certs\cert.pem, a 8011 antiga so REDIRECIONA para a 8080 (atalhos e
  etiquetas antigas de iPhone). Mude a porta com PORTA=... no .env.
  Rode num PowerShell COMO ADMINISTRADOR, de qualquer pasta:

      powershell -ExecutionPolicy Bypass -File .\deploy\instalar_servico.ps1

  Rodar de novo reinstala o servico (util depois de mudar Python ou pasta).
  Depois de cada "git pull", reinicie:  nssm restart KitConference

.PARAMETER Nome
  Nome do servico (padrao: KitConference).

.PARAMETER Python
  Caminho do python.exe. Padrao: .venv\Scripts\python.exe do projeto, se
  existir; senao, o python que estiver no PATH.
#>
param(
    [string]$Nome = "KitConference",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

# --- precisa ser administrador --------------------------------------------
$eAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $eAdmin) {
    Write-Host "Abra o PowerShell como Administrador e rode de novo." -ForegroundColor Red
    exit 1
}

# --- pasta do projeto (a pasta acima de deploy\) ---------------------------
$app = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path (Join-Path $app "run.py"))) {
    Write-Host "run.py nao encontrado em $app" -ForegroundColor Red
    exit 1
}

# --- python ----------------------------------------------------------------
if (-not $Python) {
    $venv = Join-Path $app ".venv\Scripts\python.exe"
    if (Test-Path $venv) { $Python = $venv }
    else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { $Python = $cmd.Source }
    }
}
if (-not $Python -or -not (Test-Path $Python)) {
    Write-Host "python.exe nao encontrado. Informe com -Python 'C:\caminho\python.exe'." -ForegroundColor Red
    exit 1
}
Write-Host "Python : $Python"
Write-Host "Pasta  : $app"

# --- dependencias e .env -----------------------------------------------------
if (-not (Test-Path (Join-Path $app ".env"))) {
    Write-Host "Aviso: nao ha .env na pasta (SECRET_KEY, SERVIDOR_URL...). O app pode nao subir." -ForegroundColor Yellow
}
& $Python -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalando dependencias (requirements.txt)..."
    & $Python -m pip install -r (Join-Path $app "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Write-Host "Falha ao instalar dependencias." -ForegroundColor Red; exit 1 }
}

# --- NSSM ------------------------------------------------------------------
function Achar-Nssm {
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
    Write-Host "Nao consegui instalar o NSSM. Baixe em https://nssm.cc/download, coloque o nssm.exe no PATH e rode de novo." -ForegroundColor Red
    exit 1
}
Write-Host "NSSM   : $nssm"

# --- (re)instala o servico -------------------------------------------------
if (Get-Service -Name $Nome -ErrorAction SilentlyContinue) {
    Write-Host "Servico $Nome ja existe: removendo para reinstalar..."
    & $nssm stop $Nome | Out-Null
    & $nssm remove $Nome confirm | Out-Null
}

$log = Join-Path $app "servico.log"
& $nssm install $Nome $Python "run.py" | Out-Null
& $nssm set $Nome AppDirectory $app | Out-Null
& $nssm set $Nome DisplayName "Conferencia de Kits" | Out-Null
& $nssm set $Nome Description "Conferencia de Kits (FastAPI) - HTTP 8080" | Out-Null
& $nssm set $Nome Start SERVICE_AUTO_START | Out-Null
& $nssm set $Nome AppStdout $log | Out-Null
& $nssm set $Nome AppStderr $log | Out-Null
& $nssm set $Nome AppRotateFiles 1 | Out-Null
& $nssm set $Nome AppRotateBytes 5242880 | Out-Null
& $nssm set $Nome AppExit Default Restart | Out-Null
& $nssm set $Nome AppRestartDelay 5000 | Out-Null

# PORTAL_SERVICE_KEY (se definida no Windows) e repassada ao servico.
$chave = [Environment]::GetEnvironmentVariable("PORTAL_SERVICE_KEY", "Machine")
if ($chave) { & $nssm set $Nome AppEnvironmentExtra "PORTAL_SERVICE_KEY=$chave" | Out-Null }

# --- firewall (8080 HTTP e 8011 HTTPS) --------------------------------------
if (-not (Get-NetFirewallRule -DisplayName "Kit Conference 8080-8011" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Kit Conference 8080-8011" -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort 8080,8011 | Out-Null
    Write-Host "Regra de firewall criada para as portas 8080 e 8011."
}
if (-not (Test-Path (Join-Path $app "certs\cert.pem"))) {
    Write-Host "Info: sem certs\cert.pem, a porta 8011 antiga nao redireciona (so a 8080 sobe). Normal se ninguem mais usa a 8011." -ForegroundColor Yellow
}

& $nssm start $Nome | Out-Null
Start-Sleep -Seconds 4
$st = (Get-Service -Name $Nome).Status
Write-Host ""
Write-Host "Servico $Nome : $st" -ForegroundColor Green
Write-Host "Acesse: http://localhost:8080"
Write-Host "Log   : $log"
Write-Host "Reiniciar apos git pull : nssm restart $Nome"
