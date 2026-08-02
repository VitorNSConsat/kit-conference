# Bipagem Multi-Operador — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que qualquer operador logado descubra e continue a bipagem de um kit iniciado por outro operador (via mobile), com atribuição por item de quem bipou o quê, e um indicador (fora da etiqueta) de quando um kit teve mais de um operador.

**Architecture:** Nova coluna `scan_session_items.operador_id` gravada em todo INSERT de bipagem, usando o id do operador **atualmente logado** (não mais só o dono da sessão). Duas novas funções de leitura em `app/sessions.py` agrupam os itens/operadores por sessão. `main.py` passa o `user_id` da conexão WebSocket para as funções de registro, e alimenta os templates com os dados agrupados. Nenhuma trava de permissão nova — a rota `/session/{id}` já é acessível a qualquer operador logado hoje.

**Tech Stack:** FastAPI, sqlite3 puro (via `db()` de `database.py`), Jinja2.

## Global Constraints

- Sem passo de "assumir sessão" — o operador que abrir e bipar já fica automaticamente atribuído àquele item, sem confirmação extra.
- Painel de itens bipados na tela de bipagem é **agrupado por operador** (bloco por pessoa), não uma lista única com etiqueta por item.
- Indicador de multi-operador aparece **só** em `kit_detail.html` (kit já finalizado), quando há 2+ operadores distintos. Nunca na etiqueta impressa (ZPL nem HTML).
- Lista "outros kits em andamento" no mobile mostra **todas** as sessões ativas de outros operadores, sem esconder nenhuma.
- Sem sincronização em tempo real entre abas/conexões — cada painel novo é montado a partir do banco no carregamento da página, não via WebSocket broadcast.
- "Voltar Bipagem" (desfazer último item) continua sem restrição de quem pode usar — não fica preso a "só quem bipou pode desfazer".
- Toda migração de schema segue o padrão do projeto: adicionar a condição em `_backup_antes_de_migrar()` (`database.py`) e o `ALTER TABLE` na lista de migrações, com `try/except` silencioso.

---

### Task 1: Migração — `scan_session_items.operador_id`

**Files:**
- Modify: `database.py`

**Interfaces:**
- Produces: coluna `scan_session_items.operador_id INTEGER REFERENCES users(id)` (nullable), disponível pra todas as tasks seguintes.

- [ ] **Step 1: Adicionar a condição de migração pendente**

Em `database.py`, dentro de `_backup_antes_de_migrar()`, adicione a leitura das colunas de `scan_session_items` e a condição:

```python
        colunas_scan_session = {r["name"] for r in conn.execute("PRAGMA table_info(scan_session)").fetchall()}
        colunas_veiculos = {r["name"] for r in conn.execute("PRAGMA table_info(veiculos)").fetchall()}
        colunas_scan_session_items = {r["name"] for r in conn.execute("PRAGMA table_info(scan_session_items)").fetchall()}
        tabelas = {
```

(a linha `colunas_scan_session_items = ...` é nova, logo depois de `colunas_veiculos`; o bloco `tabelas = {` já existe, só continua igual)

E no `pendente = (...)`, adicione a nova condição ao final:

```python
    pendente = (
        "admin" not in colunas_users
        or "auditoria" not in tabelas
        or "status_producao" not in colunas_kit_record
        or "nota_fiscal" not in colunas_kit_record
        or "user_permissoes_negadas" not in tabelas
        or "status_compra" not in colunas_estoque
        or "garagem" not in colunas_scan_session
        or "sequencia" not in colunas_scan_session
        or "liberado_em" not in colunas_veiculos
        or "producao_sequencia" not in tabelas
        or "operador_id" not in colunas_scan_session_items
    )
```

- [ ] **Step 2: Adicionar o `ALTER TABLE` na lista de migrações**

No `init_db()`, dentro da lista de `for stmt in [...]`, adicione ao final (antes do `]:`):

```python
            # Quem bipou cada item — permite que mais de um operador
            # continue a mesma sessão e o sistema saiba atribuir por item.
            # Nullable: linhas bipadas antes desta coluna existir ficam sem
            # atribuição (aparecem como "Sem operador registrado").
            "ALTER TABLE scan_session_items ADD COLUMN operador_id INTEGER REFERENCES users(id)",
```

- [ ] **Step 3: Verificar a migração roda sem erro**

Rode este script (cria um banco novo do zero, aplica a migração, confirma a coluna):

```bash
python -c "
import os
os.environ['DB_PATH'] = ':memory:'
from database import init_db, db
init_db()
with db() as conn:
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(scan_session_items)').fetchall()}
    assert 'operador_id' in cols, cols
print('OK: operador_id presente em scan_session_items')
"
```

Expected: `OK: operador_id presente em scan_session_items`

- [ ] **Step 4: Commit**

```bash
git add database.py
git commit -m "feat: migração — scan_session_items.operador_id"
```

---

### Task 2: `app/sessions.py` — atribuir operador por item + funções de agrupamento

**Files:**
- Modify: `app/sessions.py`

**Interfaces:**
- Consumes: coluna `scan_session_items.operador_id` (Task 1).
- Produces:
  - `register_scan(sessao_id, codigo_barra, item_tipo_id=None, operador_id=None) -> dict` (assinatura estendida)
  - `registrar_serial(sessao_id, serial_barra, operador_id=None) -> dict` (assinatura estendida)
  - `registrar_patrimonio_de_fixo(sessao_id, codigo_patrimonio, operador_id=None) -> dict` (assinatura estendida)
  - `confirmar_componente(sessao_id, codigo_barra, quantidades, operador_id=None) -> dict` (assinatura estendida)
  - `confirmar_substituicao(sessao_id, codigo_barra, motivo, operador_id=None) -> dict` (assinatura estendida)
  - `confirmar_quantidade(sessao_id, codigo_barra, quantidade, operador_id=None) -> dict` (assinatura estendida)
  - `listar_itens_por_operador(sessao_id: int) -> list[dict]` — nova. Cada item da lista: `{"operador_id": int|None, "operador_nome": str, "itens": [{"descricao": str, "codigo_barra": str, "bipado_em": str}, ...]}`.
  - `operadores_da_sessao(sessao_id: int) -> list[dict]` — nova. Cada item: `{"operador_id": int, "operador_nome": str, "total_itens": int, "primeira_bipagem": str}`.

- [ ] **Step 1: Estender `register_scan` pra receber e gravar `operador_id`**

Assinatura (linha 478):

```python
def register_scan(sessao_id: int, codigo_barra: str,
                  item_tipo_id: int | None = None,
                  operador_id: int | None = None) -> dict:
```

Os 3 `INSERT INTO scan_session_items` dentro desta função ganham a coluna `operador_id`:

Insert 1 — patrimônio fixo aguardando (era):
```python
        with db() as conn:
            conn.execute(
                "INSERT INTO scan_session_items "
                "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em) "
                "VALUES (?, ?, ?, 'aguardando_patrimonio', ?)",
                (sessao_id, codigo_barra, tipo_fixo["id"], now_brt())
            )
```
Vira:
```python
        with db() as conn:
            conn.execute(
                "INSERT INTO scan_session_items "
                "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id) "
                "VALUES (?, ?, ?, 'aguardando_patrimonio', ?, ?)",
                (sessao_id, codigo_barra, tipo_fixo["id"], now_brt(), operador_id)
            )
```

Insert 2 — itens de estoque (era):
```python
        with db() as conn:
            for seq in range(qtd):
                conn.execute(
                    "INSERT INTO scan_session_items "
                    "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em) VALUES (?, ?, ?, 'completo', ?)",
                    (sessao_id, f"ESTOQUE:{est['codigo_barra']}:{seq}", est["item_tipo_id"], now_brt())
                )
```
Vira:
```python
        with db() as conn:
            for seq in range(qtd):
                conn.execute(
                    "INSERT INTO scan_session_items "
                    "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id) "
                    "VALUES (?, ?, ?, 'completo', ?, ?)",
                    (sessao_id, f"ESTOQUE:{est['codigo_barra']}:{seq}", est["item_tipo_id"], now_brt(), operador_id)
                )
```

Insert 3 — item normal (era, perto do fim da função):
```python
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em) "
            "VALUES (?, ?, ?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"],
             "aguardando_serial" if requer_serial else "completo", now_brt())
        )
```
Vira:
```python
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"],
             "aguardando_serial" if requer_serial else "completo", now_brt(), operador_id)
        )
```

- [ ] **Step 2: Estender `registrar_serial` — forwarda `operador_id` no fallback**

Assinatura (linha 108):
```python
def registrar_serial(sessao_id: int, serial_barra: str, operador_id: int | None = None) -> dict:
    """Registra o serial number do item pendente."""
    pendente = get_pendente_serial(sessao_id)
    if not pendente:
        return register_scan(sessao_id, serial_barra, operador_id=operador_id)
```
(o resto da função não muda — o item já foi inserido com `operador_id` correto na hora que ficou `aguardando_serial`; aqui só faz `UPDATE`, sem tocar em `operador_id`.)

- [ ] **Step 3: Estender `registrar_patrimonio_de_fixo` — grava `operador_id` no insert e forwarda no fallback**

Assinatura (linha 176):
```python
def registrar_patrimonio_de_fixo(sessao_id: int, codigo_patrimonio: str, operador_id: int | None = None) -> dict:
    """Registra o patrimônio do item identificado por código fixo."""
    pendente = get_pendente_patrimonio_fixo(sessao_id)
    if not pendente:
        return register_scan(sessao_id, codigo_patrimonio, operador_id=operador_id)
```

O `INSERT` (era):
```python
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em) "
            "VALUES (?, ?, ?, ?, ?)",
            (sessao_id, codigo_patrimonio, tipo_id,
             "aguardando_serial" if requer_serial else "completo", now_brt())
        )
```
Vira:
```python
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sessao_id, codigo_patrimonio, tipo_id,
             "aguardando_serial" if requer_serial else "completo", now_brt(), operador_id)
        )
```

- [ ] **Step 4: Estender `confirmar_componente` — grava `operador_id` no insert do loop**

Assinatura (linha 420):
```python
def confirmar_componente(sessao_id: int, codigo_barra: str,
                         quantidades: dict, operador_id: int | None = None) -> dict:
```

O `INSERT` dentro do `for seq in range(adicionar):` (era):
```python
            for seq in range(adicionar):
                conn.execute(
                    "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em) "
                    "VALUES (?, ?, ?, 'completo', ?)",
                    (sessao_id, f"COMP:{codigo_barra}:{tipo_id}:{atual + seq}", tipo_id, now_brt())
                )
```
Vira:
```python
            for seq in range(adicionar):
                conn.execute(
                    "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id) "
                    "VALUES (?, ?, ?, 'completo', ?, ?)",
                    (sessao_id, f"COMP:{codigo_barra}:{tipo_id}:{atual + seq}", tipo_id, now_brt(), operador_id)
                )
```

- [ ] **Step 5: Estender `confirmar_substituicao` — grava `operador_id`**

Assinatura (linha 684):
```python
def confirmar_substituicao(sessao_id: int, codigo_barra: str, motivo: str, operador_id: int | None = None) -> dict:
```

O `INSERT` (era):
```python
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items "
            "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, observacao) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"],
             "aguardando_serial" if requer_serial else "completo", now_brt(), obs)
        )
```
Vira:
```python
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items "
            "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, observacao, operador_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"],
             "aguardando_serial" if requer_serial else "completo", now_brt(), obs, operador_id)
        )
```

- [ ] **Step 6: Estender `confirmar_quantidade` — grava `operador_id`**

Assinatura (linha 748):
```python
def confirmar_quantidade(sessao_id: int, codigo_barra: str, quantidade: float, operador_id: int | None = None) -> dict:
```

O `INSERT` (era):
```python
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items "
            "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, quantidade) "
            "VALUES (?, ?, ?, 'completo', ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"], now_brt(), quantidade)
        )
```
Vira:
```python
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items "
            "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, quantidade, operador_id) "
            "VALUES (?, ?, ?, 'completo', ?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"], now_brt(), quantidade, operador_id)
        )
```

- [ ] **Step 7: Escrever teste (falhando) para `listar_itens_por_operador` e `operadores_da_sessao`**

Adicione ao final de `tests/test_sessions.py`. O arquivo já tem um fixture `autouse` (`setup_db`) que semeia `users` id=1 ('Teste'), `item_tipo` 1/2/3, `item_master` (inclui `ANT001`/`ANT002`) e `kit_template` id=1 (2 Antenas + 1 Cabo obrigatórios) — reaproveite esse estado em vez de recriar clientes/templates:

```python
def test_listar_itens_por_operador_agrupa_por_quem_bipou():
    with db() as conn:
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash) VALUES (2, 'Maria', 'maria', 'x')"
        )

    sessao_id = sessions_mod.start_session(1, 1)  # template 1, operador 1 ("Teste")

    sessions_mod.register_scan(sessao_id, "ANT001", operador_id=1)
    sessions_mod.register_scan(sessao_id, "ANT002", operador_id=2)

    grupos = sessions_mod.listar_itens_por_operador(sessao_id)
    assert len(grupos) == 2
    assert grupos[0]["operador_nome"] == "Teste"
    assert len(grupos[0]["itens"]) == 1
    assert grupos[0]["itens"][0]["codigo_barra"] == "ANT001"
    assert grupos[1]["operador_nome"] == "Maria"
    assert len(grupos[1]["itens"]) == 1

    operadores = sessions_mod.operadores_da_sessao(sessao_id)
    assert len(operadores) == 2
    assert operadores[0]["operador_nome"] == "Teste"
    assert operadores[0]["total_itens"] == 1
    assert operadores[1]["operador_nome"] == "Maria"
```

Siga exatamente o estilo dos testes já existentes no arquivo (ex.: `test_register_scan_aceito`, logo acima) — sem fixtures novas, sem imports novos.

- [ ] **Step 8: Rodar o teste novo e confirmar que falha (funções ainda não existem)**

```bash
python -m pytest tests/test_sessions.py::test_listar_itens_por_operador_agrupa_por_quem_bipou -v
```

Expected: FAIL — `AttributeError: module 'app.sessions' has no attribute 'listar_itens_por_operador'`

- [ ] **Step 9: Implementar `listar_itens_por_operador` e `operadores_da_sessao`**

Adicione ao final de `app/sessions.py` (depois de `listar_sessoes_em_andamento`, antes ou depois de `cancel_session` — junto das outras funções de leitura de sessão):

```python
def listar_itens_por_operador(sessao_id: int) -> list[dict]:
    """Itens já bipados (status completo) nesta sessão, agrupados por quem
    bipou cada um, na ordem em que cada operador apareceu pela primeira
    vez. Linhas sem operador_id (bipadas antes desta coluna existir) ficam
    agrupadas sob a chave None, nome "Sem operador registrado"."""
    with db() as conn:
        rows = conn.execute(
            "SELECT ssi.operador_id, u.nome AS operador_nome, "
            "it.nome AS descricao, ssi.codigo_barra, ssi.bipado_em "
            "FROM scan_session_items ssi "
            "JOIN item_tipo it ON it.id = ssi.item_tipo_id "
            "LEFT JOIN users u ON u.id = ssi.operador_id "
            "WHERE ssi.sessao_id = ? AND (ssi.status IS NULL OR ssi.status = 'completo') "
            "ORDER BY ssi.bipado_em",
            (sessao_id,)
        ).fetchall()

    grupos: dict = {}
    ordem: list = []
    for r in rows:
        op_id = r["operador_id"]
        if op_id not in grupos:
            grupos[op_id] = {
                "operador_id": op_id,
                "operador_nome": r["operador_nome"] or "Sem operador registrado",
                "itens": [],
            }
            ordem.append(op_id)
        grupos[op_id]["itens"].append({
            "descricao": r["descricao"],
            "codigo_barra": r["codigo_barra"],
            "bipado_em": r["bipado_em"],
        })
    return [grupos[op_id] for op_id in ordem]


def operadores_da_sessao(sessao_id: int) -> list[dict]:
    """Operadores distintos que bipararam algo (status completo) nesta
    sessão, na ordem da primeira bipagem de cada um. Ignora linhas sem
    operador_id (sessões/itens de antes desta coluna existir)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT ssi.operador_id, u.nome AS operador_nome, COUNT(*) AS total_itens, "
            "MIN(ssi.bipado_em) AS primeira_bipagem "
            "FROM scan_session_items ssi "
            "JOIN users u ON u.id = ssi.operador_id "
            "WHERE ssi.sessao_id = ? AND ssi.operador_id IS NOT NULL "
            "AND (ssi.status IS NULL OR ssi.status = 'completo') "
            "GROUP BY ssi.operador_id "
            "ORDER BY primeira_bipagem",
            (sessao_id,)
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 10: Rodar o teste novo de novo e confirmar que passa**

```bash
python -m pytest tests/test_sessions.py::test_listar_itens_por_operador_agrupa_por_quem_bipou -v
```

Expected: PASS

- [ ] **Step 11: Rodar a suíte inteira — confirmar que só as 6 falhas pré-existentes continuam (nenhuma nova quebra)**

```bash
python -m pytest tests/ -q
```

Expected: mesmas 6 falhas de sempre em `test_sessions.py` (pré-existentes, não relacionadas a esta mudança) + o teste novo passando. Nenhuma regressão.

- [ ] **Step 12: Commit**

```bash
git add app/sessions.py tests/test_sessions.py
git commit -m "feat: rastreia operador por item bipado + agrupamento por operador"
```

---

### Task 3: `main.py` — threading do usuário logado + novos dados de contexto

**Files:**
- Modify: `main.py`

**Interfaces:**
- Consumes: `sessions_mod.listar_itens_por_operador`, `sessions_mod.operadores_da_sessao` (Task 2); todas as assinaturas estendidas com `operador_id` (Task 2).
- Produces: `session.html` recebe `itens_por_operador`; `mobile_hub.html` recebe `sessoes_outros`; `kit_detail.html` recebe `operadores_kit`.

- [ ] **Step 1: WebSocket handler — passar `user_id` como `operador_id` em toda chamada de registro**

Em `main.py`, dentro de `ws_session` (por volta da linha 1191), o bloco já tem `user_id = session_data.get("user_id")` disponível. Ajuste cada chamada:

```python
                if msg.get("acao") == "identificar":
                    result = sessions_mod.register_scan(
                        sessao_id, msg["codigo"],
                        item_tipo_id=int(msg["item_tipo_id"]),
                        operador_id=user_id,
                    )
                elif msg.get("acao") == "confirmar_quantidade":
                    result = sessions_mod.confirmar_quantidade(
                        sessao_id, msg["codigo_barra"], float(msg.get("quantidade", 1)),
                        operador_id=user_id,
                    )
                elif msg.get("acao") == "confirmar_substituicao":
                    result = sessions_mod.confirmar_substituicao(
                        sessao_id, msg["codigo_barra"], msg.get("motivo", ""),
                        operador_id=user_id,
                    )
                elif msg.get("acao") == "confirmar_componente":
                    result = sessions_mod.confirmar_componente(
                        sessao_id, msg["codigo_barra"], msg.get("quantidades", {}),
                        operador_id=user_id,
                    )
                elif msg.get("acao") == "cancelar_serial":
                    result = sessions_mod.cancelar_serial(sessao_id)
                elif msg.get("acao") == "cancelar_patrimonio_fixo":
                    result = sessions_mod.cancelar_patrimonio_fixo(sessao_id)
                elif msg.get("acao") == "desfazer_ultimo":
                    result = sessions_mod.desfazer_ultimo_item(sessao_id)
                else:
                    result = {"resultado": "rejeitado", "mensagem": "Mensagem inválida."}
            except (json.JSONDecodeError, KeyError, ValueError):
                # Plain barcode scan — priority: serial > patrimônio fixo > componente > normal
                pendente_serial = sessions_mod.get_pendente_serial(sessao_id)
                if pendente_serial:
                    result = sessions_mod.registrar_serial(sessao_id, data, operador_id=user_id)
                else:
                    pendente_fixo = sessions_mod.get_pendente_patrimonio_fixo(sessao_id)
                    if pendente_fixo:
                        result = sessions_mod.registrar_patrimonio_de_fixo(sessao_id, data, operador_id=user_id)
                    else:
                        result = sessions_mod.checar_componente(sessao_id, data)
                        if result is None:
                            result = sessions_mod.register_scan(sessao_id, data, operador_id=user_id)
```

- [ ] **Step 2: `session_page` — passar `itens_por_operador` no contexto**

Em `main.py`, na função `session_page` (por volta da linha 1092):

```python
async def session_page(request: Request, sessao_id: int):
    session = sessions_mod.get_session(sessao_id)
    if not session:
        return RedirectResponse("/", status_code=302)
    if session["status"] != "em_andamento":
        return RedirectResponse("/", status_code=302)
    if not session.get("garagem"):
        return RedirectResponse(f"/session/{sessao_id}/destino", status_code=302)
    itens = templates_mod.get_itens_template(session["kit_template_id"])
    contagem = sessions_mod.get_contagem(sessao_id)
    itens_por_operador = sessions_mod.listar_itens_por_operador(sessao_id)
    return render(request, "session.html", {
        "session": session,
        "itens": itens,
        "contagem": contagem,
        "itens_por_operador": itens_por_operador,
    })
```

(só a linha `itens_por_operador = ...` e a chave nova no dict são a mudança; o resto da função continua igual.)

- [ ] **Step 3: `mobile_hub` — listar sessões ativas de outros operadores**

Em `main.py`, dentro de `mobile_hub` (por volta da linha 1486), depois da query existente de `sessoes_ativas`, adicione:

```python
            sessoes_outros = conn.execute(
                "SELECT ss.id, kt.nome AS kit_nome, kt.cliente, ss.iniciado_em, "
                "ss.veiculo, ss.garagem, u.nome AS operador_nome "
                "FROM scan_session ss "
                "JOIN kit_template kt ON kt.id = ss.kit_template_id "
                "JOIN users u ON u.id = ss.operador_id "
                "WHERE ss.operador_id != ? AND ss.status = 'em_andamento' "
                "ORDER BY ss.iniciado_em DESC",
                (user["id"],)
            ).fetchall()
```

E adicione `"sessoes_outros": [dict(s) for s in sessoes_outros]` no `return render(...)`. Fora do `if user:` (quando não logado), `sessoes_outros` deve continuar sendo `[]` — declare junto de `sessoes_ativas = []` no topo da função:

```python
async def mobile_hub(request: Request):
    user = get_current_user(request)
    sessoes_ativas = []
    sessoes_outros = []
    templates_list = []
    if user:
        with db() as conn:
            sessoes_ativas = conn.execute(
                "SELECT ss.id, kt.nome AS kit_nome, kt.cliente, ss.iniciado_em, "
                "ss.veiculo, ss.garagem "
                "FROM scan_session ss "
                "JOIN kit_template kt ON kt.id = ss.kit_template_id "
                "WHERE ss.operador_id = ? AND ss.status = 'em_andamento' "
                "ORDER BY ss.iniciado_em DESC",
                (user["id"],)
            ).fetchall()
            sessoes_outros = conn.execute(
                "SELECT ss.id, kt.nome AS kit_nome, kt.cliente, ss.iniciado_em, "
                "ss.veiculo, ss.garagem, u.nome AS operador_nome "
                "FROM scan_session ss "
                "JOIN kit_template kt ON kt.id = ss.kit_template_id "
                "JOIN users u ON u.id = ss.operador_id "
                "WHERE ss.operador_id != ? AND ss.status = 'em_andamento' "
                "ORDER BY ss.iniciado_em DESC",
                (user["id"],)
            ).fetchall()
            templates_list = conn.execute(
                "SELECT id, nome, cliente FROM kit_template WHERE ativo = 1 ORDER BY nome"
            ).fetchall()

    return render(request, "mobile_hub.html", {
        "user": user,
        "sessoes_ativas": [dict(s) for s in sessoes_ativas],
        "sessoes_outros": [dict(s) for s in sessoes_outros],
        "templates_list": [dict(t) for t in templates_list],
    })
```

- [ ] **Step 4: `kit_detail` — passar `operadores_kit` no contexto**

Em `main.py`, na função `kit_detail` (por volta da linha 1550), depois de calcular `unidades`, adicione:

```python
    operadores_kit = sessions_mod.operadores_da_sessao(kit["sessao_id"])
```

E adicione `"operadores_kit": operadores_kit` no `return render(...)`:

```python
    return render(request, "kit_detail.html", {
        "kit": kit,
        "itens": [dict(i) for i in itens],
        "validacoes": validacoes,
        "ok": ok,
        "unidades": unidades,
        "operadores_kit": operadores_kit,
    })
```

- [ ] **Step 5: Rodar a suíte de testes pra confirmar que nada quebrou**

```bash
python -m pytest tests/ -q
```

Expected: mesmas 6 falhas pré-existentes de sempre (não relacionadas), resto passando.

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat: passa operador logado pro registro de bipagem + novos dados de contexto"
```

---

### Task 4: `templates/session.html` — painel "Itens Bipados" agrupado por operador

**Files:**
- Modify: `templates/session.html`

**Interfaces:**
- Consumes: `itens_por_operador` (Task 3) — lista de `{"operador_nome": str, "itens": [{"descricao", "codigo_barra", "bipado_em"}]}`.

- [ ] **Step 1: Adicionar o painel novo, como um `<div class="card">` a mais dentro de `.scan-layout`, depois do card de "Eventos de Bipagem"**

Em `templates/session.html`, logo depois do fechamento do card "Eventos de Bipagem" (linha 117, `</div>` que fecha `<div class="card">` do painel direito) e antes do fechamento de `.scan-layout` (linha 118, `</div>`), adicione um terceiro card:

```html
    <!-- Painel: itens já bipados, agrupados por quem bipou -->
    {% if itens_por_operador %}
    <div class="card">
        <h2>Itens Bipados</h2>
        {% for grupo in itens_por_operador %}
        <div style="margin-bottom:14px;">
            <div style="font-weight:700; font-size:13px; color:#1a3a5c; margin-bottom:6px;">
                👤 Bipado por {{ grupo.operador_nome }} ({{ grupo.itens | length }})
            </div>
            <div style="display:flex; flex-direction:column; gap:4px;">
                {% for item in grupo.itens %}
                <div style="font-size:13px; color:#555; padding:4px 8px; background:#f7f7f7; border-radius:6px;">
                    {{ item.descricao }}
                    <span style="color:#999; font-family:monospace; font-size:11px;">{{ item.codigo_barra }}</span>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endfor %}
    </div>
    {% endif %}
```

Isso torna `.scan-layout` uma grade de 3 colunas ao invés de 2 nas telas largas onde o CSS já usa grid — confira a regra `.scan-layout` no CSS global (`static/style.css` ou bloco `<style>` de `base.html`) antes de decidir: se for `grid-template-columns: 1fr 1fr` fixo, os 3 cards vão ficar desalinhados (2 numa linha, 1 sozinho embaixo) — isso é aceitável visualmente (não quebra nada), mas se preferir manter 2 colunas, o card novo pode entrar dentro do mesmo card do feed de eventos, abaixo dele, ao invés de ser um card irmão. Decida ao implementar olhando o resultado no navegador (Task 7 faz a verificação visual).

- [ ] **Step 2: Verificar balanceamento de tags**

Confirme que a contagem de `{% if %}`/`{% endif %}` e `{% for %}`/`{% endfor %}` do arquivo continua batendo, e que todo `<div>` aberto tem seu `</div>` (o snippet acima já é balanceado sozinho — só confirme que não faltou fechar nada ao colar).

- [ ] **Step 3: Commit**

```bash
git add templates/session.html
git commit -m "feat: painel de itens bipados agrupado por operador na tela de bipagem"
```

---

### Task 5: `templates/mobile_hub.html` — seção "Outros Kits em Andamento"

**Files:**
- Modify: `templates/mobile_hub.html`

**Interfaces:**
- Consumes: `sessoes_outros` (Task 3) — mesma forma de `sessoes_ativas` + campo extra `operador_nome`.

- [ ] **Step 1: Adicionar a seção, como um `<details>` nativo (sem JS), logo depois do bloco "Sessões em Andamento"**

Em `templates/mobile_hub.html`, depois do `{% endif %}` que fecha o bloco "Sessões em Andamento" (linha 282) e antes do comentário "Ações Rápidas" (linha 284), adicione:

```html
  <!-- ── Outros Kits em Andamento (de outros operadores) ──────────────── -->
  {% if sessoes_outros %}
  <details class="card" style="padding:0;">
    <summary style="padding:14px 16px; font-size:13px; font-weight:700; color:#1e4976; cursor:pointer; list-style:none;">
      👥 Ver outros kits em andamento ({{ sessoes_outros | length }})
    </summary>
    <div style="border-top:1px solid #f0ece2;">
      {% for s in sessoes_outros %}
      <a href="/session/{{ s.id }}" class="row-link">
        <div class="row-icon scan">📋</div>
        <div class="row-info">
          <div class="row-nome">{{ s.veiculo or s.kit_nome }}</div>
          <div class="row-meta">{{ s.kit_nome }} · iniciado por {{ s.operador_nome }} · {{ s.iniciado_em[:16] if s.iniciado_em else '—' }}</div>
        </div>
        <div class="row-arrow">›</div>
      </a>
      {% endfor %}
    </div>
  </details>
  {% endif %}
```

`summary { list-style: none; }` some com a seta padrão do navegador em Chrome/Safari; não é crítico se algum navegador mostrar a seta nativa mesmo assim — visual secundário, não afeta funcionamento.

- [ ] **Step 2: Verificar balanceamento de tags**

Confirme `{% if %}`/`{% endif %}`, `{% for %}`/`{% endfor %}` e `<details>/<summary>/<div>/<a>` todos fechados corretamente no trecho novo.

- [ ] **Step 3: Commit**

```bash
git add templates/mobile_hub.html
git commit -m "feat: mobile — botão para ver e continuar kits de outros operadores"
```

---

### Task 6: `templates/kit_detail.html` — seção "Operadores envolvidos"

**Files:**
- Modify: `templates/kit_detail.html`

**Interfaces:**
- Consumes: `operadores_kit` (Task 3) — lista de `{"operador_nome": str, "total_itens": int, ...}`; só renderiza se `len >= 2`.

- [ ] **Step 1: Adicionar o card, logo depois do card "Composição do Kit"**

Em `templates/kit_detail.html`, depois do `</div>` que fecha o card "Composição do Kit" (linha 361) e antes do `{% if unidades %}` (linha 363), adicione:

```html
  {% if operadores_kit and operadores_kit | length > 1 %}
  <div class="card">
    <div class="card-title">👥 Operadores envolvidos ({{ operadores_kit | length }})</div>
    {% for op in operadores_kit %}
    <div class="info-grid" style="padding:8px 0;{% if not loop.last %}border-bottom:1px solid #f0f0f0;{% endif %}">
      <div class="info-item">
        <div class="label">Operador</div>
        <div class="value">{{ op.operador_nome }}</div>
      </div>
      <div class="info-item">
        <div class="label">Itens bipados</div>
        <div class="value">{{ op.total_itens }}</div>
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}
```

Isso reaproveita as classes `.card`, `.card-title`, `.info-grid`, `.info-item`, `.label`, `.value` já usadas no resto do arquivo (mesmo padrão visual do card "Informações" e "Unidades do Pedido").

- [ ] **Step 2: Verificar balanceamento de tags**

Confirme `{% if %}`/`{% endif %}`, `{% for %}`/`{% endfor %}` e `<div>` todos fechados.

- [ ] **Step 3: Commit**

```bash
git add templates/kit_detail.html
git commit -m "feat: kit_detail mostra operadores envolvidos quando há mais de um"
```

---

### Task 7: Verificação end-to-end e commit final

**Files:**
- Create: script de verificação funcional em scratchpad (não faz parte do repositório).

- [ ] **Step 1: Script funcional cobrindo o fluxo completo**

Crie e rode um script no scratchpad (mesmo padrão dos scripts `verify_pausa_bloqueio.py` já usados neste projeto) que:
1. Cria dois usuários (operador 1 e 2), um template de kit com 2 tipos de item.
2. Operador 1 inicia sessão, define destino, bipa 1 item via `sessions_mod.register_scan(..., operador_id=op1_id)`.
3. Confirma que `sessions_mod.listar_itens_por_operador(sessao_id)` retorna 1 grupo com o item do operador 1.
4. Operador 2 bipa o segundo item via `sessions_mod.register_scan(..., operador_id=op2_id)` — mesma sessão, sem nenhum passo de "assumir".
5. Confirma que `listar_itens_por_operador` agora retorna 2 grupos (operador 1 e operador 2), cada um com seu item.
6. Confirma que `sessions_mod.operadores_da_sessao(sessao_id)` retorna os 2 operadores, na ordem de quem bipou primeiro.
7. Finaliza o kit (`session_finalize`), confirma no `kit_record` resultante que `sessions_mod.operadores_da_sessao(kit["sessao_id"])` continua retornando os 2 operadores (o kit finalizado não perde essa informação).
8. Chama a rota `main_mod.mobile_hub` autenticado como um 3º usuário (que não iniciou nenhuma sessão) e confirma que a sessão do operador 1 aparece em `sessoes_outros` (não em `sessoes_ativas`).
9. Chama `main_mod.session_page` pra essa sessão como o 3º usuário e confirma que consegue abrir normalmente (sem erro de permissão) — reflete que qualquer operador logado já podia continuar a bipagem, e agora só ficou descobrível.

- [ ] **Step 2: Rodar o script e confirmar que todos os checks passam**

```bash
python <caminho-do-script>.py
```

Expected: todos os checks `[OK]`.

- [ ] **Step 3: Verificação visual no navegador**

Suba um preview isolado (mesmo padrão de `preview_pausa.py`: DB scratch, usuário seed, 2 operadores, 1 sessão com itens de cada um) e confira via Browser:
- `/session/{id}` mostra o painel "Itens Bipados" com os dois blocos por operador.
- `/mobile` (logado como um operador que não é dono de nenhuma sessão) mostra o botão "Ver outros kits em andamento" e, ao expandir, lista a sessão do outro operador com link funcional.
- Ao finalizar esse kit, `/kit/{kit_id}` mostra a seção "Operadores envolvidos" com os 2 nomes.
- Um kit finalizado por um único operador (sem o card de multi-operador) continua sem essa seção — confirma que não aparece quando irrelevante.

- [ ] **Step 4: Rodar a suíte completa de testes uma última vez**

```bash
python -m pytest tests/ -q
```

Expected: mesmas 6 falhas pré-existentes de sempre, nada novo quebrado.

- [ ] **Step 5: Commit final (se sobrar algum ajuste feito durante a verificação)**

```bash
git add -A
git commit -m "test: verificação end-to-end da bipagem multi-operador"
git push origin master
```

(Só crie este commit se algo precisou de ajuste durante a Step 1-3; se todos os commits das Tasks 1-6 já cobrem tudo e a verificação não exigiu mudança de código, pule direto pro `git push origin master` sem commit vazio.)
