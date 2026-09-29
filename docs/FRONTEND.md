# Front-end — guia do padrão visual (e como reusar em outra aplicação)

Este documento descreve **como o front-end da Conferência de Kits é montado**: a
casca da aplicação, as cores, os componentes, os scripts globais e as regras
que toda tela segue. Serve para duas coisas:

1. **Manter** este sistema coerente quando uma tela nova é criada.
2. **Levar o mesmo visual** para uma aplicação nova (seção
   [Reusar em uma aplicação nova](#reusar-em-uma-aplicação-nova)).

> Resumo em uma linha: **HTML renderizado no servidor (FastAPI + Jinja2)**,
> **um CSS só** (`static/style.css`) guiado por **variáveis de cor**, **ícones
> SVG** num macro Jinja, e **JavaScript puro** (sem framework, sem build).

---

## 1. Stack e arquivos

| O quê | Onde | Para quê |
|---|---|---|
| Servidor de páginas | FastAPI + Jinja2 (`main.py`, função `render()`) | Monta o HTML no servidor. Nada de SPA. |
| Casca da aplicação | `templates/base.html` | Menu lateral, barra do topo, tema claro/escuro, janelas globais, scripts globais. |
| Estilo | `static/style.css` | **Todo** o CSS. Tokens em `:root`, componentes em classes. |
| Ícones | `templates/_icones.html` | Macro `ic.svg('nome', tamanho)` — SVG estilo Lucide. |
| Paginação | `templates/_paginacao.html` | Macro `pg.contagem(...)` ("Mostrando 1–50 de 241"). |
| Filtro múltiplo | `templates/_filtros.html` | Macro `f.multi(...)` (caixinhas num botão suspenso). |
| Painéis (janela lateral) | `templates/_painel*.html` | Fragmentos HTML carregados por `fetch` dentro da janela global. |
| Páginas de celular | `templates/*_mobile.html`, `kit_detail.html`, `hardware_qr.html`, `mobile_hub.html` | **Standalone** (não herdam `base.html`), com CSS próprio no `<style>`. |
| Etiquetas | `app/zpl.py` | HTML de impressão (100×150 mm, 60×60 mm), QR e código de barras. |
| Guia visual vivo | `/admin/padrao-visual` (`admin_padrao_visual.html`) | Vitrine dos componentes, para conferir no navegador. |

**Sem dependências de front-end**: nada de npm, bundler, React ou Tailwind. Só
`zxing.min.js` / `barcode-detector.js` (leitura por câmera) em `static/`.

---

## 2. Tokens (cores, raios, medidas)

Toda cor sai de uma variável em `:root`. **Nunca escreva cor fixa num
componente** — use a variável. Assim o modo escuro e uma troca de marca
funcionam sozinhos.

| Grupo | Variáveis | Uso |
|---|---|---|
| Fundo | `--bg`, `--surface`, `--surface-alt`, `--surface-hover` | Página, cartões, faixas alternadas, hover. |
| Texto | `--text`, `--text-muted`, `--text-muted-2` | Principal, secundário, terciário (datas, dicas). |
| Bordas | `--border`, `--border-strong`, `--border-subtle` | Contorno padrão, hover, divisórias leves. |
| Marca | `--brand` `#246b84`, `--brand-dark`, `--brand-soft`, `--brand-border`, `--brand-ink` | Botão primário, links, destaque, fundo suave de destaque, texto sobre fundo suave. |
| Estados | `--ok-*`, `--warn-*`, `--danger-*` (cada um com `-bg`, `-text`, `-border`) | Sucesso, atenção, erro. |
| Raios | `--radius-card` 12px, `--radius-btn` 8px, `--radius-input` 8px, `--radius-badge` 999px | |
| Casca | `--sidebar-largura` 224px, `--topbar-altura` 56px | |
| Sombra | `--shadow-card` | Sombra discreta dos cartões. |

**Tipografia:** fonte do sistema (`-apple-system, Segoe UI, Arial`), 14px no
corpo. `h1` 20px/600 (título da página, na barra do topo), `h2` 16px/600
(título de cartão). Rótulos pequenos em maiúsculas: 10–11px, `letter-spacing`
~.8px, cor `--text-muted-2`.

### Modo escuro

`:root[data-theme="dark"]` redefine **as mesmas variáveis**. O tema é aplicado
num `<script>` no `<head>` **antes do primeiro paint** (lê
`localStorage.cdk_tema`), e o botão "Modo escuro" do menu chama
`alternarTemaGlobal()`. Componente novo que só usa variáveis já funciona nos
dois temas.

### Cor por cliente

`cor_do_cliente(nome)` (injetada no Jinja por `render()`) devolve uma cor fixa
por cliente. Número do veículo, cliente e garagem aparecem **na cor do
cliente** em todas as telas — é identidade, não link.

---

## 3. Casca da página (`base.html`)

```
┌──────────────┬──────────────────────────────────────────────┐
│ .ds-sidebar  │ .ds-topbar:  [☰]  Título da tela   [ações →] │
│  logo        ├──────────────────────────────────────────────┤
│  Operação    │ .container                                   │
│   Bipagem    │   .card …                                    │
│  Cadastros   │   .card …                                    │
│  Gestão      │                                              │
│  [tema][sair]│                                              │
└──────────────┴──────────────────────────────────────────────┘
```

- **Menu lateral** (`.ds-sidebar`): gerado a partir da **mesma lista de telas
  que controla permissão** (`app/permissoes.py`, `TELAS`). Esconder o link e
  barrar a rota nunca discordam. Grupos: Operação / Cadastros / Gestão.
- **Barra do topo** (`.ds-topbar`): mostra o `{% block title %}` como título e
  o `{% block acoes_topo %}` à direita. **As ações principais da tela ficam
  aqui** (Voltar, Novo, Exportar, Editar…).
- **Conteúdo**: `{% block content %}` dentro de `.container` (máx. 1500px).
- Abaixo de 900px o menu vira gaveta (`toggleNav()`, botão ☰).

Uma tela nova, no mínimo:

```jinja
{% extends "base.html" %}
{% import "_icones.html" as ic %}
{% block title %}Nome da Tela{% endblock %}
{% block acoes_topo %}
<a href="/voltar" class="btn btn-sm btn-voltar">{{ ic.svg('voltar', 14) }} Voltar</a>
<button type="button" class="btn btn-sm btn-primary">{{ ic.svg('mais', 14) }} Novo</button>
{% endblock %}
{% block content %}
<div class="card">
  <h2>Título do cartão</h2>
  …
</div>
{% endblock %}
```

`render(request, "tela.html", {...})` sempre injeta: `user`, `pode(chave)`
(permissão), `cor_do_cliente`, `telas` e os alertas globais.

---

## 4. Componentes

### Cartão
```html
<div class="card"><h2>Título</h2> … </div>
```

### Botões
`.btn` + variante + (opcional) `.btn-sm`. Sempre **ícone + texto curto**.

| Classe | Quando |
|---|---|
| `btn-primary` | A ação principal da tela/formulário (uma por área). |
| `btn-secondary` | Ações comuns. |
| `btn-success` / `btn-warning` / `btn-danger` | Confirmar etapa / atenção / destrutivo (contorno colorido, fundo claro). |
| `btn-voltar` | "Voltar" no topo. |
| `btn-ghost` | Ação discreta ("Limpar", "Cancelar" em linha). |
| `btn-link` | Parece link, é botão. |
| `btn icon` / `btn-sm icon` | Só ícone (com `title`). |

```html
<button class="btn btn-sm btn-primary">{{ ic.svg('check', 13) }} Salvar</button>
```

### Selos (badges)
`.badge` (neutro), `.badge-success`, `.badge-warning`, `.badge-danger`,
`.badge-info`. Cor livre (ex.: status vindo do banco):
```html
<span class="badge badge-cor" style="--cor:#18804b;">Concluído</span>
```

### Avisos
`.alert.alert-success`, `.alert-danger`, `.alert-info`. Mensagens de retorno
chegam pela URL (`?ok=criado`, `?erro=...`) e a tela mostra o texto certo.

### KPIs
```html
<div class="ds-kpis">
  <div class="ds-kpi"><div><p class="ds-kpi-label">Total</p><p class="ds-kpi-value">241</p></div>
    <span class="ds-kpi-icon">{{ ic.svg('veiculos', 18) }}</span></div>
</div>
```

### Abas
`.tabs-menu` com `.tab-btn` (`.active` na atual). Cada aba é um link
(`?tab=x`) — a aba sobrevive ao F5 e pode ser compartilhada.

### Formulários
`.form-group` > `label` + campo. Em popups: `.cfg-secao` (bloco com título
`h4`), `.cfg-linha-coluna` > `.cfg-rotulo` + campo, `.cfg-ajuda` (dica
pequena), `.cfg-acoes` (botões no rodapé).

### Tabelas
`<table>` puro (o CSS já estiliza). Dentro de `<div style="overflow-x:auto">`
ou `.table-scroll` quando há muitas colunas. Linha vazia: uma `<tr>` com
`colspan` e texto `--text-muted-2`.

### Listas com busca, filtros e paginação (padrão das listas grandes)

1. **Busca ao vivo** — formulário GET com `data-busca-viva="#bloco,#badge"`:
   enquanto a pessoa digita, a mesma URL é pedida ao servidor e só os pedaços
   listados são trocados (varre **todas as páginas**, não só a aberta).
   `data-limpa-vazios` tira `campo=` vazio da URL.
   ```html
   <form method="get" action="/admin/lista" data-busca-viva="#bloco-lista" data-limpa-vazios>
     <input type="text" name="busca" value="{{ busca }}" placeholder="Buscar...">
   </form>
   <div id="bloco-lista"> … tabela + paginação … </div>
   ```
2. **Filtro por coluna** (Veículos e Clientes) — clicar no **nome da coluna**
   abre um painel (`<details class="th-filtro" data-thf>`); os campos usam
   `form="id-do-formulario"`, então busca, filtros, ordem e página andam juntos.
   Coluna filtrada fica marcada; acima da tabela ficam os **chips** de filtros
   ativos (`.thf-chip`, com × e "Limpar tudo").
3. **Filtro múltiplo** em barra — `{{ f.multi('cliente', 'Cliente', opcoes, marcados) }}`.
4. **Paginação no servidor** — `paginacao.paginar(lista, pagina)` +
   `{{ pg.contagem(pag, "veículos", total) }}` + bloco `.paginacao`.
   **Os links de página e de ordenação levam a querystring inteira** (o
   servidor monta `qs_...`): trocar de página nunca perde filtro.
5. **Seleção em lote** — caixinha por linha + "marcar todos" no cabeçalho +
   botão "Ação selecionados (N)" desabilitado enquanto nada está marcado.
   Formulários não se aninham: o botão usa `form="id"` ou um form escondido
   recebe os ids por JS.

### Janela lateral de detalhes ("painel")
Uma janela única global (`#janela-painel` no `base.html`) que carrega um
fragmento HTML por `fetch`:
```js
abrirJanelaPainel('/admin/coisa/7/painel', 'Coisa', 'subtítulo', true /* largo */);
```
O fragmento (ex.: `_painel_sobressalente.html`) **não** estende `base.html` e
usa as classes `.pat-painel`, `.pat-onde`, `.pat-campos`/`.pat-campo`,
`.pat-coluna`, `.pat-acao` (seção recolhível), `.vp-itens`/`.vp-item`.
Formulário dentro com `data-fragmento="1"` é enviado por fetch e troca só o
conteúdo da janela.

### Popup de edição / criação
```html
<button onclick="abrirPopup(event, 'editar')">Editar</button>
<div id="popup-editar" class="config-popup" role="dialog" aria-modal="true">
  <div class="config-cabecalho"><div><strong>Editar</strong><span class="config-sub">sub</span></div>
    <button class="config-fechar" onclick="fecharPopup()">&times;</button></div>
  <div class="config-corpo"> <section class="cfg-secao">…</section> <div class="cfg-acoes">…</div> </div>
</div>
```
Um aberto por vez; abrir outro de dentro **empilha** (fechar volta ao anterior
sem perder o que foi digitado). Esc fecha.

### Linha do tempo / histórico
`.hw-hist` > `.hw-hist-linha` (`.hw-hist-marca`, `.hw-hist-data`,
`.hw-hist-corpo`, `.hw-hist-autor`). Registros não se sobrescrevem: cada
mudança vira uma linha.

### Ícones
```jinja
{% import "_icones.html" as ic %}
{{ ic.svg('camera', 16) }}
```
SVG com `stroke="currentColor"` (herda a cor do texto). Disponíveis: inicio,
portal, bipagem, impressao, producao, prateleira, veiculos, itens, kits,
relatorios, rede, funcionalidades, backup, usuarios, usuario, sair, busca,
arquivo, camera, filtro, fechar, mais, mais-vertical, importar, baixar,
editar, excluir, voltar, check, check-circulo, info, chat, alerta,
seta-baixo, seta-direita, caixa, cliente, garagem, calendario, menu, relogio,
cadeado, celular, clipboard, lua, sol, paleta, historico, monitor, reiniciar.
Ícone novo: copie o `<path>` do Lucide para o dicionário `_CAMINHOS`.

---

## 5. Scripts globais (em `base.html`)

| Função | O que faz |
|---|---|
| `alternarTemaGlobal()` | Claro/escuro, salvo no navegador. |
| `toggleNav()` | Abre/fecha o menu no celular/tablet. |
| `abrirJanelaPainel(url, titulo, sub, largo)` / `fecharJanelaPainel()` | Janela lateral global. |
| `abrirPopup(event, id)` / `fecharPopup()` | Popups `.config-popup` com pilha. |
| `filtrarLista(input)` | Filtro **só na página** (`data-filtro-alvo="seletor"`) — para listas curtas, sem paginação. |
| `abrirMulti(botao)` | Filtro múltiplo (`f.multi`). |
| `ativarBuscaViva(form)` | Automático para `form[data-busca-viva]`. |
| `toggleMenu(btn)` | Menu "⋯" de ações secundárias. |

Regras: **JavaScript puro**, funções com nome em português, nada de
`localStorage` para dado de verdade (só preferência de tela), sempre com
alternativa sem JS quando possível (links e formulários comuns).

---

## 6. Páginas de celular

- As telas de operação no celular (`/mobile`, consulta de kit/item/RMA,
  estoque) são **standalone**: HTML completo com `<style>` próprio, sem o menu
  lateral. Repetem os tokens usados no topo do `<style>` (senão `var(--x)` não
  existe ali).
- Telas de admin abertas por um celular são redirecionadas para `/mobile`
  (middleware em `main.py`, lista `_MOBILE_OK_PREFIX` do que é permitido).
- **Câmera exige HTTPS** (ou localhost). Por HTTP o navegador bloqueia e o
  leitor cai para "tirar foto".
- Leitores de código: `BarcodeDetector` nativo quando existe, senão ZXing
  (`static/zxing.min.js`); todos passam pela mesma função `_abrirScanner(destino)`.
- Botões de ação rápida: `.quick-btn` com `.qb-icon` + `.qb-label`; ler com
  câmera usa sempre o ícone `camera`.

## 7. Etiquetas (impressão)

Geradas em `app/zpl.py` como HTML com `@page { size: … }` e impressão
automática. O QR sempre guarda **só uma URL** (a consulta lê o banco na hora —
nada a reimprimir quando o status muda). O **código de barras Code128** ao lado
do QR é o que o iPhone lê com mais confiança. Reaproveite `_qr_img()` e
`_barcode_img()` — são as configurações que já funcionam.

---

## 8. Regras de estilo (o que não fazer)

- **Sem emoji** na interface — use ícone SVG.
- **Sem cor fixa** em componente — use variável.
- **Sem CSS inline repetido**: se a mesma combinação aparece em 3 lugares, vira
  classe no `style.css`.
- **Ações principais no topo** (`acoes_topo`), não no fim da página.
- **Não "voltar para a página 1" sem motivo**: página e ordenação só zeram
  quando o filtro muda.
- **Filtro/busca no servidor** quando a lista é paginada (filtrar só a página
  aberta esconde resultados).
- **Texto em português**, curto e direto: "Salvar", "Arquivar selecionados (3)".
- **Arquivar, não apagar**, quando o registro tem histórico.
- Depois de mexer no `style.css`, **aumente o `?v=`** do link em `base.html`
  (`style.css?v=59` → `v=60`) — é o que força o navegador a baixar o CSS novo.

---

## Reusar em uma aplicação nova

Funciona com FastAPI + Jinja2 (ou qualquer servidor que renderize Jinja/HTML).

1. **Copie os arquivos-base**
   - `static/style.css` (inteiro; as partes `hw-*`, `pat-*`, `rem-*`, `scan-*`
     são de telas específicas e podem ser apagadas depois)
   - `templates/_icones.html`, `templates/_paginacao.html`, `templates/_filtros.html`
   - `templates/base.html` → vire o seu `base.html`: troque o logo
     (`/static/logo.png`), o subtítulo, o `NAV_INFO`/`GRUPOS_MENU` do menu e
     remova o bloco de alerta de estoque e o link do Portal se não usar.
   - `app/paginacao.py` (`paginar`, `filtrar`, `ordenar`, `janela_paginas`).
2. **Crie a função `render()`** que injeta no template: `request`, `user`,
   `pode` (permissão), `telas` (lista para o menu) e, se quiser, `cor_do_cliente`.
3. **Troque a marca**: mude só `--brand`, `--brand-dark`, `--brand-soft`,
   `--brand-border`, `--brand-ink` em `:root` (e as versões em
   `[data-theme="dark"]`). O resto acompanha.
4. **Monte as telas** com o esqueleto da seção 3 e os componentes da seção 4.
5. **Confira** abrindo uma página parecida com `/admin/padrao-visual`, nos
   dois temas e numa largura de celular.

### Esqueleto de uma lista completa (copiar e adaptar)

```jinja
{% extends "base.html" %}
{% import "_icones.html" as ic %}
{% import "_paginacao.html" as pg %}
{% block title %}Coisas{% endblock %}
{% block acoes_topo %}
<a href="/coisas/nova" class="btn btn-sm btn-primary">{{ ic.svg('mais', 14) }} Nova</a>
{% endblock %}
{% block content %}
<div class="card">
  <form method="get" action="/coisas" data-busca-viva="#bloco-coisas" data-limpa-vazios>
    <div class="filtros-linha">
      <span class="filtros-rotulo">{{ ic.svg('busca', 13) }} Busca</span>
      <input type="text" name="busca" value="{{ busca }}" style="flex:1;margin:0;" placeholder="Buscar...">
    </div>
  </form>
  <div id="bloco-coisas">
    {{ pg.contagem(pag, "coisas") }}
    {% if pag.itens %}
    <div style="overflow-x:auto;">
    <table>
      <thead><tr><th>Nome</th><th>Status</th><th></th></tr></thead>
      <tbody>
      {% for c in pag.itens %}
      <tr>
        <td><strong>{{ c.nome }}</strong></td>
        <td><span class="badge badge-cor" style="--cor:{{ c.cor }};">{{ c.status }}</span></td>
        <td><a href="/coisas/{{ c.id }}" class="btn btn-sm btn-secondary">Ver</a></td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
    {% if pag.total_paginas > 1 %}
    <div class="paginacao">
      <span class="paginacao-info">página {{ pag.pagina }} de {{ pag.total_paginas }}</span>
      {% for p in pag.paginas_visiveis %}
        {% if p is none %}<span class="paginacao-ellipsis">…</span>
        {% else %}<a href="?{{ qs }}{{ '&' if qs }}pagina={{ p }}"
             class="btn btn-sm {{ 'btn-primary' if p == pag.pagina else 'btn-secondary' }}">{{ p }}</a>{% endif %}
      {% endfor %}
    </div>
    {% endif %}
    {% else %}
    <p style="color:var(--text-muted-2);text-align:center;padding:24px;">Nada encontrado.</p>
    {% endif %}
  </div>
</div>
{% endblock %}
```

```python
@app.get("/coisas", response_class=HTMLResponse)
async def coisas(request: Request, busca: str = "", pagina: int = 1):
    from urllib.parse import urlencode
    lista = paginacao_mod.filtrar(listar_coisas(), busca, ("nome", "status"))
    return render(request, "coisas.html", {
        "busca": busca,
        "pag": paginacao_mod.paginar(lista, pagina),
        "qs": urlencode([("busca", busca)] if busca else []),   # paginação mantém a busca
    })
```

### Checklist de uma tela nova

- [ ] Título no `block title` e ações no `acoes_topo`.
- [ ] Só variáveis de cor; ícones do `_icones.html`; nada de emoji.
- [ ] Lista grande: busca no servidor + paginação + querystring preservada.
- [ ] Estado vazio com texto ("Nenhum … encontrado").
- [ ] Mensagem de sucesso/erro via `?ok=` / `?erro=`.
- [ ] Funciona no tema escuro e a 375px de largura.
- [ ] `style.css?v=` incrementado se o CSS mudou.
