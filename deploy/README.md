# Deploy — Conferência de Kits

Padrão único: **uma porta, 8080 (HTTP)**, rodando como serviço do Windows
(`KitConference`, via NSSM). Notebooks da rede interna usam `http://IP:8080`;
celulares e acesso externo usam o endereço público do Cloudflare Tunnel
(`SERVIDOR_URL` no `.env`), que já entrega HTTPS — sem certificado para instalar.

## Scripts (PowerShell como Administrador)

| O que fazer | Comando |
|---|---|
| Instalar / reinstalar o serviço | `powershell -ExecutionPolicy Bypass -File .\deploy\instalar_servico.ps1` |
| Atualizar (git pull + dependências + reinício + teste) | `powershell -ExecutionPolicy Bypass -File .\deploy\atualizar_servico.ps1` |
| Remover o serviço (não apaga banco/arquivos) | `powershell -ExecutionPolicy Bypass -File .\deploy\remover_servico.ps1` |

Rotina de atualização: o `atualizar_servico.ps1` faz o `git pull`, instala as
dependências, reinicia e confere `http://localhost:8080/ping`.

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
