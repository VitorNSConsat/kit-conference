# Deploy — Conferência de Kits

Padrão único: **uma porta, 8080 (HTTP)**, rodando como serviço do Windows
(`KitConference`, via NSSM). Notebooks da rede interna usam `http://IP:8080`;
celulares e acesso externo usam o endereço público do Cloudflare Tunnel
(`SERVIDOR_URL` no `.env`), que já entrega HTTPS — sem certificado para instalar.

## Scripts (PowerShell como Administrador)

A aplicação roda **somente como Serviço do Windows** (`KitConference`, via NSSM),
sem Tarefa Agendada e sem depender de login, terminal, `.bat` ou Python manual:

    Windows → Serviço KitConference → python.exe run.py → aplicação

| O que fazer | Comando |
|---|---|
| Instalar / reinstalar o serviço | `powershell -ExecutionPolicy Bypass -File .\deploy\instalar_servico.ps1` |
| Conferir (`-Ciclo` também para/inicia/reinicia) | `powershell -ExecutionPolicy Bypass -File .\deployalidar_servico.ps1 -Ciclo` |
| Atualizar (git pull + dependências + reinício + teste) | `powershell -ExecutionPolicy Bypass -File .\deploytualizar_servico.ps1` |
| Remover o serviço (não apaga banco/arquivos) | `powershell -ExecutionPolicy Bypass -File .\deploy\desinstalar_servico.ps1` |

O instalador acha o Python (usa `.venv` se existir), instala o NSSM se preciso (em
`C:\Program Files
ssm`), remove a Tarefa Agendada antiga do app (se houver), **desativa**
sem apagar qualquer outra tarefa que aponte para esta pasta e mantém a
`PORTAL_SERVICE_KEY` que o serviço já tinha.

Teste do boot (manual, uma vez): reinicie o Windows **sem fazer login** e, de outro
computador, abra `http://IP-DO-SERVIDOR:8080`. O `start.bat` fica só para rodar
manualmente em desenvolvimento.

## Operação do dia a dia

```powershell
Get-Service KitConference          # está rodando?
nssm restart KitConference         # reiniciar
Get-Content .\servico.log -Tail 50 # últimas linhas do log
```

Portas em uso: `Get-NetTCPConnection -State Listen | Where LocalPort -in 8080,8011`.

## Configuração (`.env`)

| Variável | Para quê |
|---|---|
| `SERVIDOR_URL` | Endereço que vai no QR das etiquetas (o domínio público, se houver) |
| `PORTA` | Porta HTTP do app (padrão 8080) |
| `REDIRECIONAR_8011` | `0` desliga o redirecionamento da 8011 antiga |

## A porta 8011 (legado)

Aparelhos que ainda abrem `https://IP:8011` (atalho salvo, etiqueta antiga) caem
num redirecionamento para o endereço atual, enquanto existir `certs/cert.pem`.
Quando ninguém mais usar, ponha `REDIRECIONAR_8011=0` e reinicie.
iPhone com atalho antigo em tela branca: apagar o atalho e adicionar de novo pelo
endereço novo.

## Backup

O banco é `kit_conference.db` (a rotina de backup do próprio sistema fica em Backup).
Nenhum script daqui mexe nele.
