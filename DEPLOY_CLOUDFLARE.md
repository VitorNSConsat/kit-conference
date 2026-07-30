# Expondo o sistema com Cloudflare Tunnel

Guia para acessar o Conferência de Kits de fora do galpão, com HTTPS válido
e sem abrir nenhuma porta no roteador.

## Como funciona

O app **continua rodando na máquina de sempre**. O `cloudflared` abre uma
conexão *de saída* até a Cloudflare e o tráfego volta por dentro dela:

```
Navegador → Cloudflare (TLS + Access) → túnel → cloudflared → localhost:8080
```

Consequências práticas:

- Nenhuma porta aberta no roteador, nenhum IP fixo necessário.
- Certificado TLS válido de verdade — acaba o aviso de "site não seguro"
  que o certificado autoassinado dá hoje no celular.
- `git pull` continua valendo exatamente como hoje, porque o app roda
  onde sempre rodou. Depois do pull, reinicie o serviço do app.
- **A máquina precisa continuar ligada.** O túnel não hospeda nada; ele só
  publica o que está rodando aqui.

## Pré-requisito

Um domínio com os nameservers apontando para a Cloudflare (o domínio
precisa aparecer no painel dela). Para só testar antes de comprar domínio,
dá para usar um túnel efêmero:

```bash
cloudflared tunnel --url http://localhost:8080
```

Isso devolve uma URL aleatória `*.trycloudflare.com`, sem autenticação e
que morre quando você fecha. Serve para provar o conceito, **não** para uso
real.

## 1. Instalar o cloudflared

Baixe o `.msi` de 64 bits em
`https://github.com/cloudflare/cloudflared/releases` e instale. Depois,
no PowerShell:

```bash
cloudflared --version
```

## 2. Autenticar e criar o túnel

```bash
cloudflared tunnel login
```

Abre o navegador para você escolher o domínio. Em seguida:

```bash
cloudflared tunnel create kit-conference
```

Anote o UUID que ele imprime — o arquivo de credenciais vai para
`C:\Users\<voce>\.cloudflared\<UUID>.json`.

## 3. Arquivo de configuração

Crie `C:\Users\<voce>\.cloudflared\config.yml`:

```yaml
tunnel: kit-conference
credentials-file: C:\Users\<voce>\.cloudflared\<UUID>.json

ingress:
  - hostname: kits.seudominio.com.br
    service: http://localhost:8080
  - service: http_status:404
```

Aponte para a porta **8080** (HTTP puro local), não a 8011. O TLS quem faz
é a Cloudflare na borda; internamente é tudo localhost. WebSocket
(`/ws/session/...`, usado na bipagem) passa pelo túnel sem configuração
extra.

## 4. Publicar o DNS

```bash
cloudflared tunnel route dns kit-conference kits.seudominio.com.br
```

## 5. Rodar como serviço do Windows

Para subir junto com a máquina:

```bash
cloudflared service install
```

## 6. Ajustar o `.env` do app

Três variáveis passam a importar:

```
SERVIDOR_URL=https://kits.seudominio.com.br
COOKIE_SECURE=1
TRUST_PROXY_IP=1
```

- **`SERVIDOR_URL`** é o endereço que vai no **QR code das etiquetas**. Sem
  isso o app detecta o IP da LAN e toda etiqueta impressa sai apontando
  para `https://192.168.x.x:8011`, que não abre fora do galpão.
- **`COOKIE_SECURE=1`** marca o cookie de sessão como "só por HTTPS".
  ⚠️ Ligue **apenas** quando o acesso for só pelo domínio. Se alguém ainda
  entra pela LAN em `http://192.168...:8080`, o navegador para de mandar o
  cookie e ninguém consegue logar por lá.
- **`TRUST_PROXY_IP=1`** faz o app ler o IP real do usuário no cabeçalho
  `CF-Connecting-IP`. Sem isso, o limite de tentativas de login enxerga
  todo mundo como o mesmo IP (o da Cloudflare) e um usuário errando a senha
  tranca os outros. ⚠️ Só ligue quando o acesso passar mesmo pelo túnel —
  quem fala direto com o app consegue forjar esse cabeçalho.

Reinicie o app depois de editar.

## 7. Cloudflare Access — a parte que protege

Sem Access, o túnel publica o sistema para a internet inteira. No painel
**Zero Trust → Access → Applications**, crie a aplicação self-hosted
apontando para `kits.seudominio.com.br` e uma política **Allow** listando
os e-mails de quem pode entrar. A partir daí a Cloudflare pede identidade
*antes* de a requisição chegar no app.

### O detalhe que quebra as etiquetas se você não fizer

Estas rotas são públicas **de propósito** — é o QR da etiqueta, o instalador
precisa abrir no celular sem login:

```
/kit/{id}      /kit/buscar      /mobile
/estoque/{id}  /estoque/buscar  /prateleira/tv
```

Se o Access trancar tudo, **o QR das etiquetas para de funcionar**. Crie uma
segunda aplicação, mais específica, com política **Bypass**:

| Aplicação | Caminho | Política |
|---|---|---|
| Kits — verificação (QR) | `kits.seudominio.com.br/kit` | Bypass (todos) |
| Kits — sistema | `kits.seudominio.com.br` | Allow (e-mails da equipe) |

O Access resolve pelo caminho mais específico, então `/kit/...` cai no
Bypass e o resto exige login.

**Decida conscientemente** o que mais liberar. Cada rota da lista acima que
você deixar em Bypass fica visível para qualquer um com o link — e
`/kit/buscar` e `/estoque/buscar` permitem *varrer* o acervo, não só abrir
um item conhecido. Se o instalador só precisa abrir o QR já impresso,
libere `/kit` e deixe o resto atrás do login.

Se for liberar `/prateleira/tv` (o painel da TV), libere também `/static`,
porque essa tela carrega o logo de lá. As telas de kit e estoque têm o CSS
embutido e não precisam.

## Checklist antes de expor

- [ ] `SECRET_KEY` no `.env` é longo e aleatório (o código tem
      `"dev-secret"` como fallback — numa máquina sem `.env` ele entra em
      silêncio e permite forjar sessão)
- [ ] `.env` não está no Git (já está no `.gitignore`)
- [ ] Senhas dos usuários trocadas — as de teste não vão para a internet
- [ ] Access com política Allow configurada, e o Bypass **só** no que
      precisa ser público
- [ ] Testado o QR de uma etiqueta nova pelo celular na rede móvel (fora do
      Wi-Fi do galpão)
- [ ] `/cert` (baixar o certificado autoassinado) não faz mais sentido
      exposto — deixe atrás do Allow

## Atualizando depois

```bash
git pull
```

E reinicie o app. O túnel não precisa de nada: ele aponta para a porta
local, não para uma versão do código.
