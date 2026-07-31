# Acessando o sistema de fora — guia para a equipe

Este guia é para quem só precisa **usar** o sistema fora do galpão (celular
ou notebook), sem mexer em configuração da Cloudflare. Quem administra tem
o guia técnico completo em `DEPLOY_CLOUDFLARE.md`.

**Antes de começar, peça ao administrador:**
- O **nome da equipe** Cloudflare (ex: `consat`) — necessário no login
- Confirmação de que seu **e-mail** está autorizado a entrar

---

## iPhone / iPad

### 1. Instalar e conectar

1. Baixe **Cloudflare One Agent** na App Store (ícone laranja).
2. Abra o app → **Login with Cloudflare Zero Trust**.
3. Digite o nome da equipe (ex: `consat`) → **Next**.
4. Digite seu e-mail autorizado → **Send me a code**.
5. Abra o e-mail, copie o código de 6 dígitos, cole na tela e confirme.

   > ⚠️ Se aparecer **"this one-time PIN has already been used"** mesmo com
   > o código recém-chegado: toque em **"Request new code"** e digite bem
   > rápido. Isso acontece quando o filtro de segurança do e-mail
   > corporativo (Outlook/Microsoft 365) "abre" o link do código sozinho
   > antes de você ler. Se persistir, peça ao TI para liberar
   > `noreply@notify.cloudflare.com` do Safe Links.

6. O iOS vai pedir permissão para **configurar uma VPN** — toque em
   **Permitir**. É assim que o túnel funciona, não é vírus nem app espião.
7. Confirme que o app mostra **"Connected"**. Se estiver desconectado,
   toque para conectar.

### 2. Abrindo o sistema

1. Com o app **conectado**, abra o **Safari**.
2. Digite: `https://192.168.1.232:8011`
3. Vai aparecer um aviso de site não seguro / certificado inválido —
   **isso é esperado**, o certificado é próprio da empresa, não emitido por
   uma autoridade pública. Toque em **"Show Details"** (ou "Detalhes") →
   **"visit this website"** (ou "Continuar mesmo assim").
4. Você cai na tela de login do sistema — entre com seu usuário e senha de
   sempre (esse login é diferente do e-mail que você acabou de usar).

### 3. (Opcional) Fazer o aviso de certificado sumir de vez

Só vale a pena se você for acessar com frequência nesse mesmo aparelho:

1. No Safari, acesse `https://192.168.1.232:8011/cert` (baixa um arquivo).
2. **Ajustes → Perfil Baixado → Instalar** (peça a senha do aparelho se
   solicitado).
3. **Ajustes → Geral → Sobre → Confiança de Certificado** → ative o
   certificado na lista.

Depois disso, o cadeado aparece sem aviso nenhum, todas as vezes.

### 4. Se o Safari bloquear com "a navegação falhou" antes mesmo do aviso de certificado

Isso é um bloqueio **diferente** — o Safari recusando qualquer HTTP puro.
Só acontece se você tentar acessar via `http://` (porta 8080) em vez de
`https://` (porta 8011). Prefira sempre o link com `https://` acima. Se
mesmo assim precisar mexer nisso:

**Ajustes → Apps → Safari** → seção **Privacidade e Segurança** → desative
**"Usar Somente HTTPS"** (ou use a busca do app Ajustes e digite "HTTPS").

---

## Notebook (Windows ou Mac)

### 1. Instalar e conectar

1. Baixe o **Cloudflare One Client** no site oficial da Cloudflare
   (`developers.cloudflare.com/cloudflare-one/team-and-resources/devices/cloudflare-one-client/download/`).
2. Instale normalmente.
3. Abra o app (ícone fica na bandeja do Windows / barra de menu do Mac).
4. **Login** → nome da equipe → e-mail → código recebido (mesmo processo
   do celular, mesmo aviso de "código já usado" pode acontecer).
5. Confirme **"Connected"**.

### 2. Abrindo o sistema

1. Abra o navegador (Chrome, Edge, Firefox — qualquer um).
2. Acesse `https://192.168.1.232:8011`.
3. Vai aparecer o aviso de certificado — em navegador de computador é mais
   simples:
   - **Chrome/Edge:** clique em **"Avançado"** → **"Prosseguir para
     192.168.1.232 (não seguro)"**.
   - **Firefox:** clique em **"Avançado"** → **"Aceitar o Risco e
     Continuar"**.
4. Faça login no sistema normalmente.

Em notebook não costuma valer a pena instalar o certificado no sistema —
o clique em "Avançado" leva 2 segundos e o navegador lembra por um tempo.

---

## Perguntas rápidas

**Preciso deixar o app do Cloudflare sempre aberto?**
Sim. Se ele desconectar, o sistema simplesmente fica **carregando para
sempre**, sem mensagem de erro — pareça que travou, mas é só o túnel que
caiu. Reabra o app, confirme "Connected", tente de novo.

**Esqueci a senha do sistema (não é a do e-mail/Cloudflare).**
Fale com um administrador — ele troca pela tela `/admin/usuarios`.

**Funciona em qualquer rede (4G, Wi-Fi de casa, etc)?**
Sim, contanto que o Cloudflare One Agent esteja instalado, logado e
conectado. É exatamente esse o objetivo: acesso de qualquer lugar sem
precisar estar na rede do galpão.

---

## Referência técnica — como a rede do app está montada

Para quem for dar suporte à equipe ou mexer nessa configuração depois:

| Porta | Protocolo | Uso |
|---|---|---|
| **8011** | HTTPS (certificado autoassinado) | Porta recomendada para acesso externo — funciona em qualquer navegador, com o aviso de certificado descrito acima |
| **8080** | HTTP puro | Só funciona se o navegador não tiver "HTTPS-Only" ativado. No Safari do iOS isso trava com "a navegação falhou" |

- O certificado de 8011 é gerado por `python gerar_cert.py`, **direto na
  máquina servidor** (o script grava o IP atual dela dentro do
  certificado — rodar em outra máquina gera certificado para o IP errado).
- A rota `/cert` (`https://192.168.1.232:8011/cert`) serve esse mesmo
  certificado para download e instalação manual em iOS/Android.
- Se o IP fixo da máquina mudar, o certificado antigo passa a apontar para
  um IP que não existe mais e precisa ser gerado de novo.
- A variável `SOMENTE_HTTPS=1` no `.env` do servidor desliga a porta 8080
  de vez, deixando só a 8011 — só ative depois de confirmar que ninguém
  mais depende do acesso por HTTP puro (veja `DEPLOY_CLOUDFLARE.md`).
