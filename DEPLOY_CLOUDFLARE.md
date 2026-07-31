# Acessando o sistema de fora com Cloudflare

Guia para usar o Conferência de Kits fora do galpão sem abrir nenhuma
porta no roteador.

Existem **dois caminhos**, e eles resolvem problemas diferentes.

| | Rede Privada (WARP) | Hostname público + Access |
|---|---|---|
| Domínio próprio | **não precisa** | obrigatório (~US$5–10/ano) |
| Exposto na internet | **nada** | o app, com o Access na porta |
| Nos aparelhos | precisa instalar o cliente | nada, abre em qualquer navegador |
| Quem pode acessar | só quem você autoriza na organização | qualquer um que passe no Access |
| Custo | grátis até 50 usuários | grátis até 50 usuários + o domínio |

**Se todo mundo que acessa (inclusive quem escaneia o QR) é da empresa, use
a Rede Privada.** É mais seguro e não custa nada: sem hostname público, um
atacante de fora não tem nem endereço para tentar. A diferença é entre "uma
porta com fechadura boa" e "não existe porta".

O caminho do hostname público só se torna necessário quando alguém **de
fora da empresa** (cliente final, terceirizado) precisa abrir o QR.

Os dois usam o mesmo túnel — dá para começar pela Rede Privada e adicionar
o hostname público depois, sem refazer nada.

---

# Caminho A — Rede Privada (sem domínio)

## 1. Ativar o Zero Trust

No painel da Cloudflare, entre em **Zero Trust**, escolha um nome de equipe
(vira `suaempresa.cloudflareaccess.com`, fornecido de graça) e selecione o
plano **Free** — até 50 usuários, sem cartão.

## 2. Fixar o IP do servidor ⚠️

Antes de qualquer coisa, garanta que a máquina do sistema tenha **IP fixo**
(reserva de DHCP no roteador).

Isso não é detalhe: o IP vai **impresso no QR de cada etiqueta**. Se o
roteador trocar o IP da máquina, todas as etiquetas já impressas param de
funcionar — e papel não se atualiza.

## 3. Criar o túnel

Instale o conector:

```bash
winget install --id Cloudflare.cloudflared
```

No painel: **Networks → Tunnels → Create a tunnel → Cloudflared**. Dê um
nome (ex: `galpao`) e copie o comando de instalação que ele mostra — já vem
com o token, e instala como serviço do Windows (sobe junto com a máquina).

## 4. Rotear só o servidor

Na aba **Private Network** do túnel, adicione o IP da máquina com máscara
`/32`:

```
192.168.1.85/32
```

Use `/32` (só o servidor), **não** a faixa inteira `192.168.1.0/24` — senão
qualquer aparelho autorizado passa a enxergar a rede inteira do escritório,
não apenas o sistema de kits.

## 5. Liberar a faixa privada no Split Tunnel ⚠️

**Este é o passo que faz tudo funcionar ou nada funcionar.**

Por padrão o cliente **exclui** as faixas de rede privada (RFC 1918) do
túnel — e o IP do servidor está exatamente dentro delas. Se você pular isto,
o resto fica configurado corretamente e mesmo assim **não funciona**, sem
mensagem de erro: só dá timeout.

O painel da Cloudflare vem reorganizando esse menu (o cliente que se
chamava "WARP" agora é o **Cloudflare One Client**), então o caminho muda
conforme a versão do seu painel:

- **Painel novo:** Zero Trust → **Team & Resources → Devices → Device
  profiles** → abra o perfil (geralmente chamado *"Default"* ou *"General
  profile"*) → **Configure** → role até **Split Tunnels** → **Manage**.
- **Painel antigo:** Zero Trust → **Settings → WARP Client → Device
  settings** → abra o perfil → **Configure** → **Split Tunnels** → **Manage**.

Se mesmo assim não achar, use a busca do painel (ícone de lupa no topo) e
digite "split tunnel" — ela leva direto à tela, independente do menu.

No modo *Exclude*, **remova** a entrada:

```
192.168.0.0/16
```

Efeito colateral: a partir daí *todo* tráfego `192.168.x.x` do aparelho vai
pelo túnel — inclusive o roteador da casa do funcionário, que quase sempre
também é `192.168.1.x`. Costuma ser inofensivo, mas pode atrapalhar coisas
locais (impressora de casa, Chromecast). Para evitar de vez, a rede do
galpão precisaria usar uma faixa incomum (ex: `10.42.x.x`) em vez de
`192.168.1.x`.

## 6. Autorizar os aparelhos

Em **Settings → Authentication** (painel antigo, também pode aparecer como
**Team & Resources → Device enrollment permissions** no novo), defina quem
pode entrar na organização — normalmente uma regra por domínio de e-mail
(ex: qualquer `@suaempresa.com.br`) ou lista de e-mails específicos.

Em cada aparelho:

| Plataforma | App |
|---|---|
| iOS | **Cloudflare One Agent** (App Store) |
| Android | **Cloudflare One Agent** (Google Play) |
| Windows / macOS | **Cloudflare One client** (site da Cloudflare) |

No celular: abrir o app → **Login with Cloudflare Zero Trust** → digitar o
nome da equipe → autenticar → **permitir a configuração de VPN** (o iOS pede
isso explicitamente) → deixar conectado.

> A Cloudflare separou o app corporativo do `1.1.1.1` de consumidor. O que
> serve aqui é o **One Agent**.

Pronto: com o cliente ligado, `http://192.168.1.85:8080` abre de qualquer
rede do mundo, e escanear o QR da etiqueta funciona normalmente.

**Avise a equipe:** se o VPN estiver desligado, a etiqueta simplesmente não
abre — e a falha é silenciosa, parece que o sistema caiu. Vale a instrução
*"se o QR não abrir, confira se o Cloudflare está conectado"*.

## 7. Ajustar o `.env`

```
SERVIDOR_URL=http://192.168.1.85:8080
```

Fixe explicitamente em vez de deixar o app detectar sozinho — assim o QR
sai sempre igual, mesmo que a detecção falhe algum dia.

Repare que **o mesmo endereço funciona dentro e fora do galpão**: na LAN vai
direto, fora vai pelo cliente. Uma URL só, etiquetas sempre válidas.

`COOKIE_SECURE` e `TRUST_PROXY_IP` ficam **desligados** neste caminho (não há
HTTPS público nem proxy na frente).

Bônus: como o tráfego já é cifrado pela Cloudflare de ponta a ponta, dá para
usar a porta 8080 (HTTP) e abandonar o certificado autoassinado — acabam os
avisos de "site não seguro".

---

# Caminho B — Hostname público + Access (precisa de domínio)

Use este caminho **apenas** se gente de fora da empresa precisar abrir o QR.

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

---

# Vale para os dois caminhos

## Perfis de usuário e auditoria

O sistema tem **dois perfis**. O usuário comum faz tudo no dia a dia —
bipa, finaliza kit, imprime, cadastra e edita. A única coisa que ele não
pode é **excluir**. Administrador pode excluir e gerir usuários.

A checagem é no **servidor** (`require_admin`), não só escondendo o botão:
chamar a rota de exclusão direto devolve **403**. Toda ação que altera
dados fica gravada em `/admin/auditoria`, junto com tentativas negadas —
útil para perceber alguém sondando o que consegue acessar.

Na primeira vez que o sistema sobe com esta versão, **todos os usuários
que já existiam viram administradores**, para ninguém perder de uma vez o
acesso que tinha ontem. Revise em `/admin/usuarios` e rebaixe quem deve
ser comum. O sistema impede rebaixar ou desativar o último administrador
ativo.

Usuários não são excluídos, apenas desativados — o histórico de kits e
movimentações aponta para eles. Desativar corta o acesso na hora, sem
esperar o cookie expirar.

## Checklist antes de liberar o acesso

- [ ] `SECRET_KEY` no `.env` é longo e aleatório. Com `COOKIE_SECURE=1` ou
      `SERVIDOR_URL` em `https://`, o app **se recusa a subir** sem ela —
      subir com a chave padrão permitiria forjar sessão de qualquer usuário
- [ ] Revisados os perfis em `/admin/usuarios` (a migração promove todo
      mundo a admin de propósito, para não quebrar a operação)
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
