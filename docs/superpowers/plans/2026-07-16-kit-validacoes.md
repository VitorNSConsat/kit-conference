# Kit Validações Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow internal users to validate assembled kits via mobile QR scan, record who validated and when, and export that audit trail as a report and Excel file.

**Architecture:** New `kit_validacoes` table referenced by `kit_id`. Validation action lives on the existing public `/kit/{kit_id}` page — login check done manually (not via `require_login` decorator) so the page stays public for viewing. New `/reports/validacoes` page mirrors the existing `/reports` pattern. Main reports table gains a validation-count column via LEFT JOIN subquery.

**Tech Stack:** FastAPI, SQLite via `database.db()` context manager, Jinja2 templates, openpyxl (already installed), `now_brt()` from `database.py`.

## Global Constraints

- All timestamps use `now_brt()` from `database.py` — never `CURRENT_TIMESTAMP` or `datetime.now()`
- Auth: use `get_current_user(request)` from `app.auth` — never hardcode user checks
- DB: always use `with db() as conn:` context manager from `database.py`
- Templates: `kit_detail.html` is standalone (no `base.html`); new report template extends `base.html`
- Follow existing openpyxl style: `azul="1A3A5C"`, `branco="FFFFFF"`, `cinza="F4F7FB"`, `hdr_cell()` helper pattern

---

### Task 1: Database table + validacoes module

**Files:**
- Modify: `database.py` — add `CREATE TABLE IF NOT EXISTS kit_validacoes`
- Create: `app/validacoes.py` — three functions: `registrar`, `listar_por_kit`, `listar_relatorio`

**Interfaces:**
- Produces:
  - `registrar(kit_id: str, user_id: int, observacao: str) -> int` — returns new row id
  - `listar_por_kit(kit_id: str) -> list[dict]` — returns `[{id, kit_id, validado_por, validado_em, observacao, user_nome}]`
  - `listar_relatorio(data_ini: str, data_fim: str, user_id: str) -> list[dict]` — returns `[{kit_id, kit_nome, cliente, veiculo, garagem, operador_nome, finalizado_em, validado_por_nome, validado_em, observacao, itens_resumo}]`

- [ ] **Step 1: Add table to database.py**

Open `database.py` and find the `_SCHEMA` string (or wherever `CREATE TABLE` statements live). Add after the last existing table:

```python
CREATE TABLE IF NOT EXISTS kit_validacoes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id       TEXT    NOT NULL REFERENCES kit_record(kit_id),
    validado_por INTEGER NOT NULL REFERENCES users(id),
    validado_em  TEXT    NOT NULL,
    observacao   TEXT
);
```

- [ ] **Step 2: Restart server (or run migration) to create table**

The `db()` context manager creates tables on first use. Restart the server, then verify:

```bash
python -c "from database import db; [print(r[0]) for r in db().__enter__().execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]"
```

Expected output includes: `kit_validacoes`

- [ ] **Step 3: Create `app/validacoes.py`**

```python
from database import db, now_brt


def registrar(kit_id: str, user_id: int, observacao: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO kit_validacoes (kit_id, validado_por, validado_em, observacao) "
            "VALUES (?, ?, ?, ?)",
            (kit_id, user_id, now_brt(), observacao or None)
        )
        return cur.lastrowid


def listar_por_kit(kit_id: str) -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT kv.*, u.nome AS user_nome "
            "FROM kit_validacoes kv "
            "JOIN users u ON u.id = kv.validado_por "
            "WHERE kv.kit_id = ? "
            "ORDER BY kv.validado_em DESC",
            (kit_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def listar_relatorio(data_ini: str = "", data_fim: str = "", user_id: str = "") -> list:
    query = """
        SELECT kv.id, kv.kit_id, kv.validado_em, kv.observacao,
               uv.nome AS validado_por_nome,
               kr.finalizado_em, kr.veiculo, kr.garagem,
               kt.nome AS kit_nome, kt.cliente,
               uo.nome AS operador_nome,
               (
                   SELECT GROUP_CONCAT(sub.r, ' | ')
                   FROM (
                       SELECT it.nome || ' x' || COUNT(*) AS r
                       FROM scan_session_items si
                       JOIN item_tipo it ON it.id = si.item_tipo_id
                       WHERE si.sessao_id = kr.sessao_id
                       GROUP BY si.item_tipo_id
                   ) sub
               ) AS itens_resumo
        FROM kit_validacoes kv
        JOIN kit_record kr ON kr.kit_id = kv.kit_id
        JOIN kit_template kt ON kt.id = kr.kit_template_id
        JOIN users uv ON uv.id = kv.validado_por
        JOIN users uo ON uo.id = kr.operador_id
        WHERE 1=1
    """
    params = []
    if data_ini:
        query += " AND DATE(kv.validado_em) >= ?"
        params.append(data_ini)
    if data_fim:
        query += " AND DATE(kv.validado_em) <= ?"
        params.append(data_fim)
    if user_id:
        query += " AND kv.validado_por = ?"
        params.append(int(user_id))
    query += " ORDER BY kv.validado_em DESC LIMIT 500"
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Commit**

```bash
git add database.py app/validacoes.py
git commit -m "feat: tabela kit_validacoes e módulo de validações"
```

---

### Task 2: Mobile validation flow

**Files:**
- Modify: `main.py` — update `GET /kit/{kit_id}` to pass `validacoes` + `user`; add `POST /kit/{kit_id}/validar`
- Modify: `templates/kit_detail.html` — add validation section at bottom

**Interfaces:**
- Consumes: `validacoes_mod.registrar(kit_id, user_id, observacao)`, `validacoes_mod.listar_por_kit(kit_id)`
- Consumes: `get_current_user(request)` from `app.auth`

- [ ] **Step 1: Import module and update GET route in main.py**

Find the import block at the top of `main.py` where other `app.*` modules are imported and add:

```python
import app.validacoes as validacoes_mod
```

Find the existing `kit_detail` route (around line 623) and replace it with:

```python
@app.get("/kit/{kit_id}", response_class=HTMLResponse)
async def kit_detail(request: Request, kit_id: str):
    user = get_current_user(request)
    with db() as conn:
        kit = conn.execute(
            "SELECT kr.*, kt.nome AS kit_nome, kt.cliente, kt.versao, "
            "u.nome AS operador_nome "
            "FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "JOIN users u ON u.id = kr.operador_id "
            "WHERE kr.kit_id = ?",
            (kit_id,)
        ).fetchone()
        if not kit:
            return HTMLResponse("<h2>Kit não encontrado.</h2>", status_code=404)
        kit = dict(kit)

        itens = conn.execute(
            "SELECT it.nome AS tipo_nome, COUNT(*) AS quantidade, "
            "GROUP_CONCAT(si.codigo_barra, ', ') AS barcodes "
            "FROM scan_session_items si "
            "JOIN item_tipo it ON it.id = si.item_tipo_id "
            "WHERE si.sessao_id = ? "
            "GROUP BY si.item_tipo_id ORDER BY it.nome",
            (kit["sessao_id"],)
        ).fetchall()

    validacoes = validacoes_mod.listar_por_kit(kit_id)
    ok = request.query_params.get("ok", "")

    return render(request, "kit_detail.html", {
        "kit": kit,
        "itens": [dict(i) for i in itens],
        "validacoes": validacoes,
        "ok": ok,
    })
```

Note: usar `render()` (padrão do projeto) — ele já injeta `user` no contexto do template automaticamente, então o template pode usar `{{ user }}` diretamente.

- [ ] **Step 2: Add POST route in main.py**

Add immediately after the updated `kit_detail` route:

```python
@app.post("/kit/{kit_id}/validar")
async def kit_validar(request: Request, kit_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    observacao = str(form.get("observacao", "")).strip()
    with db() as conn:
        exists = conn.execute(
            "SELECT kit_id FROM kit_record WHERE kit_id = ?", (kit_id,)
        ).fetchone()
    if not exists:
        return HTMLResponse("<h2>Kit não encontrado.</h2>", status_code=404)
    validacoes_mod.registrar(kit_id, user["id"], observacao)
    return RedirectResponse(f"/kit/{kit_id}?ok=validado", status_code=302)
```

- [ ] **Step 3: Add validation section to kit_detail.html**

In `templates/kit_detail.html`, add these CSS rules inside the `<style>` block (before the closing `</style>`):

```css
  /* ── Validação ────────────────────────────── */
  .val-form {
    background: #f0f8f2;
    border: 1.5px solid #27ae60;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 14px;
  }
  .val-form textarea {
    width: 100%;
    padding: 12px;
    font-size: 15px;
    border: 1.5px solid #ddd;
    border-radius: 8px;
    resize: vertical;
    min-height: 70px;
    margin: 8px 0 12px;
    font-family: inherit;
    box-sizing: border-box;
  }
  .val-btn {
    width: 100%;
    padding: 15px;
    background: #27ae60;
    color: #fff;
    border: none;
    border-radius: 10px;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
  }
  .val-btn:active { background: #1e8449; }
  .val-login-btn {
    display: block;
    width: 100%;
    padding: 15px;
    background: #1a3a5c;
    color: #fff;
    border: none;
    border-radius: 10px;
    font-size: 16px;
    font-weight: 700;
    text-align: center;
    text-decoration: none;
    box-sizing: border-box;
  }
  .val-history-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding: 10px 0;
    border-bottom: 1px solid #f0f0f0;
    gap: 10px;
  }
  .val-history-row:last-child { border-bottom: none; }
  .val-who { font-size: 14px; font-weight: 600; color: #1a3a5c; }
  .val-when { font-size: 12px; color: #999; }
  .val-obs { font-size: 13px; color: #555; margin-top: 2px; }
  .val-check { font-size: 22px; flex-shrink: 0; }
  .alert-success {
    background: #d4edda; color: #155724;
    border: 1px solid #c3e6cb; border-radius: 8px;
    padding: 12px 16px; margin-bottom: 14px;
    font-size: 14px; font-weight: 600;
  }
```

Then, inside `<div class="container">`, add the following block **after** the "Composição do Kit" card and **before** the closing `</div>`:

```html
  {% if ok == 'validado' %}
  <div class="alert-success">✅ Validação registrada com sucesso.</div>
  {% endif %}

  <!-- Validação -->
  <div class="card">
    <div class="card-title">Validação do Kit</div>

    {% if user %}
    <div class="val-form">
      <div style="font-size:14px;font-weight:600;color:#1a3a5c;margin-bottom:4px;">
        Validando como: <strong>{{ user.nome }}</strong>
      </div>
      <form method="post" action="/kit/{{ kit.kit_id }}/validar">
        <textarea name="observacao" placeholder="Observação (opcional)..."></textarea>
        <button type="submit" class="val-btn">✅ Confirmar Validação</button>
      </form>
    </div>
    {% else %}
    <p style="font-size:13px;color:#888;margin-bottom:12px;">
      Faça login para validar este kit.
    </p>
    <a href="/login" class="val-login-btn">🔒 Entrar para Validar</a>
    {% endif %}

    {% if validacoes %}
    <div style="margin-top:16px;">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;
                  letter-spacing:1.5px;color:#888;margin-bottom:8px;">
        Histórico de validações
      </div>
      {% for v in validacoes %}
      <div class="val-history-row">
        <div>
          <div class="val-who">{{ v.user_nome }}</div>
          <div class="val-when">{{ v.validado_em[:16] }}</div>
          {% if v.observacao %}
          <div class="val-obs">{{ v.observacao }}</div>
          {% endif %}
        </div>
        <div class="val-check">✅</div>
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>
```

- [ ] **Step 4: Verify manually**

1. Start the server and open `/kit/{qualquer_kit_id}` sem estar logado → deve mostrar botão "🔒 Entrar para Validar"
2. Logar e abrir o mesmo kit → deve mostrar formulário com textarea e botão verde
3. Submeter → deve redirecionar de volta com banner verde "✅ Validação registrada"
4. Validação deve aparecer no histórico abaixo do formulário

- [ ] **Step 5: Commit**

```bash
git add main.py templates/kit_detail.html
git commit -m "feat: validação de kit mobile com histórico"
```

---

### Task 3: Relatório de validações + export Excel

**Files:**
- Modify: `main.py` — add `GET /reports/validacoes` and `GET /reports/validacoes/export`
- Create: `templates/reports_validacoes.html`

**Interfaces:**
- Consumes: `validacoes_mod.listar_relatorio(data_ini, data_fim, user_id)`

- [ ] **Step 1: Add routes in main.py**

Find the reports section in `main.py` (around line 655) and add the following two routes after the existing report routes:

```python
@app.get("/reports/validacoes", response_class=HTMLResponse)
@require_login
async def reports_validacoes(request: Request,
                             data_ini: str = "",
                             data_fim: str = "",
                             validador_id: str = ""):
    rows = validacoes_mod.listar_relatorio(data_ini, data_fim, validador_id)
    with db() as conn:
        usuarios = conn.execute("SELECT id, nome FROM users ORDER BY nome").fetchall()
    return render(request, "reports_validacoes.html", {
        "rows": rows,
        "usuarios": [dict(u) for u in usuarios],
        "data_ini": data_ini,
        "data_fim": data_fim,
        "validador_id": validador_id,
    })


@app.get("/reports/validacoes/export")
@require_login
async def reports_validacoes_export(request: Request,
                                    data_ini: str = "",
                                    data_fim: str = "",
                                    validador_id: str = ""):
    from fastapi.responses import Response as _Resp
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO

    rows = validacoes_mod.listar_relatorio(data_ini, data_fim, validador_id)

    azul = "1A3A5C"
    branco = "FFFFFF"
    cinza = "F4F7FB"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Validações"

    headers = [
        "Kit ID", "Template", "Cliente", "Veículo", "Garagem",
        "Operador Conferência", "Data Conferência",
        "Validado Por", "Data Validação", "Observação", "Itens"
    ]
    widths = [14, 28, 22, 14, 14, 22, 20, 22, 20, 30, 50]

    for col, (h, w) in enumerate(zip(headers, widths), 1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = Font(bold=True, color=branco)
        c.fill = PatternFill("solid", fgColor=azul)
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[ws.cell(1, col).column_letter].width = w

    for i, r in enumerate(rows, 2):
        ws.cell(i, 1, r["kit_id"][:8].upper())
        ws.cell(i, 2, r["kit_nome"])
        ws.cell(i, 3, r["cliente"])
        ws.cell(i, 4, r.get("veiculo") or "")
        ws.cell(i, 5, r.get("garagem") or "")
        ws.cell(i, 6, r["operador_nome"])
        ws.cell(i, 7, r.get("finalizado_em", ""))
        ws.cell(i, 8, r["validado_por_nome"])
        ws.cell(i, 9, r["validado_em"])
        ws.cell(i, 10, r.get("observacao") or "")
        ws.cell(i, 11, r.get("itens_resumo") or "")
        if i % 2 == 0:
            for col in range(1, 12):
                ws.cell(i, col).fill = PatternFill("solid", fgColor=cinza)

    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return _Resp(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=validacoes.xlsx"},
    )
```

- [ ] **Step 2: Create `templates/reports_validacoes.html`**

```html
{% extends "base.html" %}
{% block title %}Relatório de Validações{% endblock %}
{% block content %}
<h1>Relatório de Validações</h1>

<div class="card">
  <h2>Filtros</h2>
  <form method="get" style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;">
    <div class="form-group" style="margin:0;">
      <label>Data inicial</label>
      <input type="date" name="data_ini" value="{{ data_ini }}">
    </div>
    <div class="form-group" style="margin:0;">
      <label>Data final</label>
      <input type="date" name="data_fim" value="{{ data_fim }}">
    </div>
    <div class="form-group" style="margin:0;">
      <label>Validador</label>
      <select name="validador_id">
        <option value="">— todos —</option>
        {% for u in usuarios %}
        <option value="{{ u.id }}" {{ 'selected' if validador_id == u.id|string }}>{{ u.nome }}</option>
        {% endfor %}
      </select>
    </div>
    <button type="submit" class="btn btn-primary">🔍 Filtrar</button>
    <a href="/reports/validacoes/export?data_ini={{ data_ini }}&data_fim={{ data_fim }}&validador_id={{ validador_id }}"
       class="btn btn-success">📥 Exportar Excel</a>
  </form>
</div>

<div class="card">
  <h2>Validações ({{ rows | length }})</h2>
  {% if rows %}
  <div style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>Data Validação</th>
        <th>Kit</th>
        <th>Cliente</th>
        <th>Veículo</th>
        <th>Operador</th>
        <th>Validado Por</th>
        <th>Observação</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
    {% for r in rows %}
    <tr>
      <td style="white-space:nowrap;">{{ r.validado_em[:16] }}</td>
      <td><strong>{{ r.kit_nome }}</strong><br>
          <span style="font-size:11px;color:#888;font-family:monospace;">{{ r.kit_id[:8].upper() }}</span>
      </td>
      <td>{{ r.cliente }}</td>
      <td>{{ r.veiculo or '—' }}</td>
      <td>{{ r.operador_nome }}</td>
      <td><strong>{{ r.validado_por_nome }}</strong></td>
      <td style="font-size:13px;color:#555;">{{ r.observacao or '—' }}</td>
      <td>
        <a href="/kit/{{ r.kit_id }}" target="_blank"
           class="btn btn-sm btn-primary">Ver Kit</a>
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p style="color:#888;text-align:center;padding:24px;">
    Nenhuma validação encontrada para os filtros selecionados.
  </p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 3: Verify manually**

1. Abrir `/reports/validacoes` → deve mostrar a tabela com as validações feitas na Task 2
2. Testar filtro por data → tabela atualiza
3. Clicar "📥 Exportar Excel" → baixa arquivo `validacoes.xlsx` com todas as colunas corretas

- [ ] **Step 4: Commit**

```bash
git add main.py templates/reports_validacoes.html
git commit -m "feat: relatório de validações com export Excel"
```

---

### Task 4: Badge de validações no relatório principal + link de acesso

**Files:**
- Modify: `main.py` — update `/reports` query to count validações por kit
- Modify: `templates/reports.html` — add column "Validações" + link para `/reports/validacoes`

**Interfaces:**
- Consumes: `kit_validacoes` table (via inline subquery in reports SQL)

- [ ] **Step 1: Update /reports query in main.py**

Find the `reports` route (around line 657). Update the SELECT to include a validation count subquery:

```python
    query = """
        SELECT kr.kit_id, kr.finalizado_em, kr.status,
               kr.veiculo, kr.garagem,
               kt.nome AS kit_nome, kt.cliente, kt.versao,
               u.nome AS operador_nome,
               pq.id AS pq_id,
               (SELECT COUNT(*) FROM kit_validacoes kv WHERE kv.kit_id = kr.kit_id) AS num_validacoes
        FROM kit_record kr
        JOIN kit_template kt ON kt.id = kr.kit_template_id
        JOIN users u ON u.id = kr.operador_id
        LEFT JOIN print_queue pq ON pq.kit_id = kr.kit_id
        WHERE 1=1
    """
```

(Only the SELECT list changes — all other WHERE/ORDER logic stays the same.)

- [ ] **Step 2: Update reports.html**

Open `templates/reports.html`. Find the `<thead>` row of the kits table and add a "Validações" column header. Then in the `<tbody>` row, add the corresponding cell.

In `<thead>`, add after the last existing `<th>`:
```html
<th>Validações</th>
```

In the `<tbody>` row (where each kit is rendered), add after the last existing `<td>`:
```html
<td style="text-align:center;">
  {% if kit.num_validacoes > 0 %}
  <a href="/kit/{{ kit.kit_id }}" target="_blank"
     style="color:#27ae60;font-weight:700;text-decoration:none;">
    ✅ {{ kit.num_validacoes }}
  </a>
  {% else %}
  <span style="color:#ccc;font-size:13px;">⚠️ Nenhuma</span>
  {% endif %}
</td>
```

Also add a link to the new validações report page. Find the `<h1>` or page header area and add:

```html
<a href="/reports/validacoes" class="btn btn-primary" style="float:right;margin-top:-4px;">
  📋 Relatório de Validações
</a>
```

- [ ] **Step 3: Verify manually**

1. Abrir `/reports` → nova coluna "Validações" visível
2. Kits sem validação → "⚠️ Nenhuma" em cinza
3. Kit validado (da Task 2) → "✅ 1" em verde clicável
4. Botão "📋 Relatório de Validações" leva para `/reports/validacoes`

- [ ] **Step 4: Final commit + push**

```bash
git add main.py templates/reports.html
git commit -m "feat: coluna de validações no relatório principal e link de acesso"
git push
```
