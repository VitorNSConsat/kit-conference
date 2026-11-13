"""Pacote do front-end (Sistema → Padrão visual → Baixar o pacote do front-end).

Monta, na hora do download, um .zip com o "esqueleto visual" do sistema pra
replicar em outras aplicações: a mesma cara (cores, componentes, barra do topo
com os menus, janelas, Ir para…), sem levar nenhuma regra deste sistema.

Tudo sai dos arquivos de verdade, lidos no momento do download — o pacote
nunca fica desatualizado em relação ao que está no ar:

- static/style.css e templates/_icones.html vão inteiros;
- templates/base.html vai SEM os trechos marcados entre os comentários
  ``kit:especifico`` e ``kit:fim`` (aviso de estoque, janelas de veículo e
  patrimônio, Enter dos leitores de código de barras…);
- app/menu.py vai com a lista de grupos trocada por um exemplo.
"""
import io
import json
import re
import zipfile
from pathlib import Path

from database import now_brt

RAIZ = Path(__file__).resolve().parent.parent

_ESPECIFICO = re.compile(r"\{# kit:especifico #\}.*?\{# kit:fim #\}", re.S)

_MENU_EXEMPLO = '''_GRUPOS = [
    # Entrada DIRETA (abre num clique): (nome, ícone, _a(...)).
    ("Início", "inicio", _a("Início", "inicio", "/", [], ["/"])),
    # Grupo com painel: (nome, ícone, [atalhos e submenus]).
    ("Cadastros", "kits", [
        _a("Clientes", "cliente", "/clientes", [], ["/clientes"]),
        # Submenu = uma coluna com título no painel da barra do topo.
        _sub("Produtos", "itens", [
            _a("Lista de produtos", "itens", "/produtos", [], ["/produtos"]),
            _a("Novo produto", "mais", "/produtos/novo", []),
        ]),
    ]),
    ("Relatórios", "relatorios", [
        _a("Resumo do mês", "relatorios", "/relatorios", [], ["/relatorios"]),
        # tipo="download" baixa um arquivo; tipo="tv" abre numa aba nova.
        _a("Exportar tudo", "baixar", "/relatorios/exportar.xlsx", [], tipo="download"),
    ]),
    ("Sistema", "funcionalidades", [
        _a("Usuários", "usuarios", "/usuarios", [], ["/usuarios"], admin=True),
    ]),
]
'''


def _ler(rel: str) -> str:
    return (RAIZ / rel).read_text(encoding="utf-8")


def base_generico() -> str:
    """O base.html do sistema sem o que é só deste sistema."""
    base = _ESPECIFICO.sub("", _ler("templates/base.html"))
    # o comentário que explica as marcas não faz sentido fora daqui
    return re.sub(r"\{# Pacote do front-end.*?#\}\n", "", base, count=1, flags=re.S)


def menu_generico() -> str:
    """app/menu.py com a lista de grupos de exemplo (as funções ficam iguais)."""
    s = _ler("app/menu.py")
    ini = s.index("_GRUPOS = [")
    fim = s.index("\n]\n", ini) + len("\n]\n")
    s = s[:ini] + _MENU_EXEMPLO + s[fim:]
    s = re.sub(r"^_CURTO = \{.*?\}$", "_CURTO = {}   # nome curto na barra: {\"Nome comprido\": \"Curto\"}",
               s, count=1, flags=re.M)
    s = re.sub(r"^_ABA_PADRAO = \{.*?\}$", "_ABA_PADRAO = {}", s, count=1, flags=re.M)
    s = re.sub(r"^_RELATORIOS_PROPRIOS = \(.*?\)$", "_RELATORIOS_PROPRIOS = ()", s, count=1, flags=re.M)
    return s


def tokens(css: str) -> dict:
    """As variáveis de cor/medida do :root (claro) e do modo escuro."""
    def bloco(seletor: str) -> dict:
        i = css.index(seletor)
        corpo = css[css.index("{", i) + 1:css.index("\n}", i)]
        corpo = re.sub(r"/\*.*?\*/", "", corpo, flags=re.S)
        return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", corpo)}
    return {"claro": bloco(":root {"), "escuro": bloco(':root[data-theme="dark"] {')}


def icones() -> list[str]:
    """Nomes dos ícones disponíveis em templates/_icones.html."""
    s = _ler("templates/_icones.html")
    corpo = s[s.index("_CAMINHOS = {"):s.index("} %}")]
    return re.findall(r"^\s*'([\w-]+)'\s*:", corpo, flags=re.M)


_APP_EXEMPLO = '''"""Exemplo mínimo: uma aplicação FastAPI usando o pacote do front-end.

Rodar:  pip install fastapi uvicorn jinja2
        uvicorn exemplo.app:app --reload        (de dentro da pasta do pacote)

O que a sua aplicação precisa fazer é só o render() abaixo: entregar ao
base.html o usuário, a função pode() e o menu montado (app/menu.py). As telas,
as rotas e as regras continuam sendo as da SUA aplicação.
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import app.menu as menu_mod

RAIZ = Path(__file__).resolve().parent.parent
app = FastAPI()
app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")
jinja = Jinja2Templates(directory=[str(RAIZ / "exemplo" / "templates"), str(RAIZ / "templates")])


def usuario_logado(request: Request) -> dict:
    # Troque pelo login da sua aplicação. O base.html usa: nome e admin.
    return {"id": 1, "nome": "Usuário Exemplo", "admin": True}


def render(request: Request, template: str, ctx: dict | None = None):
    user = usuario_logado(request)
    pode = lambda chave: True            # troque pelas permissões da sua aplicação
    grupos = menu_mod.montar(request.url.path, request.url.query, user, pode)
    return jinja.TemplateResponse(request, template, {
        "user": user, "pode": pode,
        "menu_grupos": grupos,
        "menu_trilha": menu_mod.trilha(grupos),
        "menu_ir_para": menu_mod.ir_para(grupos),
        **(ctx or {}),
    })


@app.get("/", response_class=HTMLResponse)
async def inicio(request: Request):
    return render(request, "inicio.html")


@app.get("/{caminho:path}", response_class=HTMLResponse)
async def qualquer_tela(request: Request, caminho: str):
    # Só pra navegar pelo menu de exemplo; na sua aplicação cada tela é sua.
    titulo = caminho.strip("/").split("/")[-1].replace("-", " ").capitalize()
    return render(request, "inicio.html", {"titulo": titulo})
'''

_PAGINA_EXEMPLO = '''{% extends "base.html" %}
{% import "_icones.html" as ic %}
{% block title %}{{ titulo or "Início" }}{% endblock %}
{% block acoes_topo %}
<button type="button" class="btn btn-secondary">{{ ic.svg('baixar', 15) }} Exportar</button>
<button type="button" class="btn btn-primary" onclick="abrirPopup(event, 'exemplo')">{{ ic.svg('mais', 15) }} Novo registro</button>
{% endblock %}
{% block content %}
<section class="ds-kpis">
  <div class="ds-kpi"><div><p class="ds-kpi-label">Em andamento</p><p class="ds-kpi-value">12</p><p class="ds-kpi-sub">3 prontos</p></div>
    <span class="ds-kpi-icon">{{ ic.svg('relogio', 20) }}</span></div>
  <div class="ds-kpi"><div><p class="ds-kpi-label">Concluídos hoje</p><p class="ds-kpi-value">48</p></div>
    <span class="ds-kpi-icon success">{{ ic.svg('check-circulo', 20) }}</span></div>
  <a href="#" class="ds-kpi"><div><p class="ds-kpi-label">Pendências</p><p class="ds-kpi-value">5</p><p class="ds-kpi-sub">abrir lista</p></div>
    <span class="ds-kpi-icon">{{ ic.svg('alerta', 20) }}</span></a>
</section>

<div class="card">
  <h2>Registros</h2>
  <div class="busca-form"><input type="text" placeholder="Buscar..." oninput="filtrarLista(this)" data-filtro-alvo="#tabela-exemplo tbody tr"></div>
  <div class="table-scroll">
    <table id="tabela-exemplo">
      <thead><tr><th>Código</th><th>Nome</th><th>Situação</th><th></th></tr></thead>
      <tbody>
        <tr><td>A-001</td><td>Primeiro registro</td><td><span class="badge badge-success">Concluído</span></td>
          <td><button type="button" class="btn btn-sm btn-secondary">Ver</button></td></tr>
        <tr><td>A-002</td><td>Segundo registro</td><td><span class="badge badge-warning">Em andamento</span></td>
          <td><button type="button" class="btn btn-sm btn-secondary">Ver</button></td></tr>
        <tr><td>A-003</td><td>Terceiro registro</td><td><span class="badge badge-danger">Cancelado</span></td>
          <td><button type="button" class="btn btn-sm btn-secondary">Ver</button></td></tr>
      </tbody>
    </table>
  </div>
</div>

{# Janela (pop-up): abrirPopup(event, 'exemplo') abre; Esc, X ou clique fora fecham. #}
<div id="popup-exemplo" class="config-popup" role="dialog" aria-modal="true" style="width:min(520px, calc(100vw - 32px));">
  <div class="config-cabecalho">
    <div><strong>Novo registro</strong><span class="config-sub">Exemplo de janela do padrão visual.</span></div>
    <button type="button" class="config-fechar" onclick="fecharPopup()" title="Fechar (Esc)">&times;</button>
  </div>
  <div style="padding:16px 22px;">
    <div class="form-group"><label>Nome</label><input type="text" placeholder="ex: Registro novo"></div>
  </div>
  <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 22px;border-top:1px solid var(--border);background:var(--surface-alt);">
    <button type="button" class="btn btn-ghost" onclick="fecharPopup()">Cancelar</button>
    <button type="button" class="btn btn-primary" onclick="fecharPopup()">{{ ic.svg('check', 15) }} Salvar</button>
  </div>
</div>
{% endblock %}
'''


def _readme(versao_css: str, nomes_icones: list[str]) -> str:
    return f"""# Pacote do front-end — padrão visual Consat

Gerado em {now_brt()[:16]} a partir do sistema Conferência de Kits (CSS v{versao_css}).
Serve pra dar a MESMA CARA a outra aplicação — cores, barra do topo com os
menus, janelas, tabelas, botões, Ir para (Ctrl K) — sem levar nenhuma regra
deste sistema. Cada aplicação continua com as próprias telas e funções.

## O que vem no pacote

| Arquivo | O que é | Mexer? |
|---|---|---|
| `static/style.css` | Todo o visual: tokens de cor (claro e escuro), casca, componentes | Não — é o padrão. Estilo próprio da sua aplicação vai num CSS seu, carregado depois |
| `templates/base.html` | Casca: barra do topo, painéis dos grupos, gaveta do celular, Ir para, título da tela, janelas | Não — as telas fazem `{{% extends "base.html" %}}` |
| `templates/_icones.html` | Biblioteca de ícones (SVG, traço) — `{{{{ ic.svg('nome', 18) }}}}` | Pode acrescentar ícones novos |
| `app/menu.py` | Monta o menu (grupos, submenus, item ativo, caminho, Ir para) | Sim: só a lista `_GRUPOS` |
| `static/logo.png` | Logo Consat | Troque se a aplicação tiver outro nome |
| `tokens.json` | As mesmas cores em JSON (pra outra linguagem/ferramenta) | Só leitura |
| `exemplo/` | Aplicação mínima (FastAPI) mostrando tudo funcionando | É um modelo |

## Como usar numa aplicação

1. Copie `static/`, `templates/base.html`, `templates/_icones.html` e `app/menu.py`.
2. No `app/menu.py`, troque `_GRUPOS` pelas telas da sua aplicação:
   - entrada direta (abre num clique): `("Nome", "icone", _a("Nome", "icone", "/rota", [perms], ["/prefixo"]))`;
   - grupo com painel: `("Nome", "icone", [_a(...), _sub("Submenu", "icone", [_a(...), ...])])`;
   - cada `_sub` vira uma coluna com título no painel; `admin=True` mostra só pra admin;
     `tipo="download"` baixa arquivo e `tipo="tv"` abre em aba nova.
3. Em toda resposta de tela, entregue ao template (veja `exemplo/app.py`, função `render`):
   `user` (com `nome` e `admin`), `pode(chave)` (permissões), `menu_grupos`,
   `menu_trilha` e `menu_ir_para` — os três últimos saem de `app/menu.py`.
4. Cada tela: `{{% extends "base.html" %}}` com os blocos `title`, `acoes_topo` (botões do canto
   direito do título), `content` e, se precisar, `scripts`.

O link do botão Portal (à esquerda do logo) aponta pra `http://<mesmo host>:8001/`.

## Regras do padrão

- **Cores só por token** (`var(--brand)`, `var(--surface)`…), nunca hex no template — o modo escuro
  troca tudo sozinho. Paleta: fundo `--bg` cinza-azulado, cartões `--surface` brancos, marca
  `--brand` azul Consat. Semânticas sempre em par fundo suave + texto: `--ok-*`, `--warn-*`, `--danger-*`.
- **Um botão primário por tela** (`.btn-primary`). `.btn-success` só confirma, `.btn-danger` só destrói;
  o resto é `.btn-secondary` ou `.btn-ghost`. Tamanhos: padrão (36px) e `.btn-sm` (30px).
- **Menu na barra do topo**: passar o mouse num grupo abre o painel (com atraso curto pra não abrir
  de passagem e não trocar na diagonal); clicar também abre (toque e teclado). A barra fica mais
  compacta até 1400px e, quando os grupos não cabem (zoom, tela estreita), vira o ☰ com a gaveta.
- **Título da tela** sai do bloco `title`; o caminho no menu aparece em cima ("Grupo › Submenu").
- **Janelas**: `.config-popup` com `abrirPopup(event, 'id')` / `fecharPopup()` (Esc, X e clique fora
  fecham; uma janela aberta de dentro de outra empilha e o Esc volta pra anterior). Conteúdo vindo do
  servidor: `abrirJanelaPainel(url, titulo, subtitulo, largo)`.
- **Números do topo** (`.ds-kpis` > `.ds-kpi`): ícone à esquerda, número grande, rótulo embaixo; vira
  `<a>`/`<button>` quando abre algo.
- **Tabelas** dentro de `.table-scroll`; quando não cabem, aparece o aviso "a tabela continua para o lado".
- **Menu "⋯"** de ações secundárias: `.menu-mais` + `toggleMenu(this)`.
- **Busca na lista**: `<input oninput="filtrarLista(this)" data-filtro-alvo="seletor das linhas">`.
- **Ir para (Ctrl K)**: acha qualquer tela do menu pelo nome; vem pronto do `menu_ir_para`.
- Componentes extras no `style.css`: esteira de etapas (`.esteira`), janela de criação com catálogo e
  itens lado a lado (`.ck-*`), filtro de múltipla escolha (`.multi`), abas (`.tabs-menu`), paginação.

## Ícones disponíveis

{", ".join(nomes_icones)}

## Atualizar

Baixe o pacote de novo em **Sistema → Configurações → Padrão visual** do Conferência de Kits:
ele é montado na hora a partir do que está no ar.
"""


def montar_zip() -> bytes:
    css = _ler("static/style.css")
    base = _ler("templates/base.html")
    m = re.search(r"/static/style\.css\?v=(\d+)", base)
    versao = m.group(1) if m else "?"
    nomes = icones()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        pasta = "frontend-consat/"
        z.writestr(pasta + "README.md", _readme(versao, nomes))
        z.writestr(pasta + "static/style.css", css)
        for img in ("logo.png", "icon-192.png"):
            arq = RAIZ / "static" / img
            if arq.exists():
                z.write(arq, pasta + "static/" + img)
        z.writestr(pasta + "templates/base.html", base_generico())
        z.writestr(pasta + "templates/_icones.html", _ler("templates/_icones.html"))
        z.writestr(pasta + "app/__init__.py", "")
        z.writestr(pasta + "app/menu.py", menu_generico())
        z.writestr(pasta + "exemplo/__init__.py", "")
        z.writestr(pasta + "exemplo/app.py", _APP_EXEMPLO)
        z.writestr(pasta + "exemplo/templates/inicio.html", _PAGINA_EXEMPLO)
        z.writestr(pasta + "tokens.json", json.dumps(tokens(css), ensure_ascii=False, indent=2))
    return buf.getvalue()
