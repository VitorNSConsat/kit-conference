import io
from database import db, now_brt


# ── Tipos de item ──────────────────────────────────────────────────────────────

def listar_tipos(apenas_ativos: bool = False) -> list:
    with db() as conn:
        if apenas_ativos:
            rows = conn.execute(
                "SELECT * FROM item_tipo WHERE ativo = 1 ORDER BY nome"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM item_tipo ORDER BY nome"
            ).fetchall()
    return [dict(r) for r in rows]


def listar_tipos_para_kit(template_id: int) -> list:
    """Retorna apenas os tipos presentes no template (para o modal de identificação)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT it.id, it.nome FROM item_tipo it "
            "JOIN kit_template_items ki ON ki.item_tipo_id = it.id "
            "WHERE ki.kit_template_id = ? AND it.ativo = 1 ORDER BY it.nome",
            (template_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def criar_tipo(nome: str, unidade: str = "un") -> int:
    unidade = unidade if unidade in ("un", "m") else "un"
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO item_tipo (nome, unidade, criado_em) VALUES (?, ?, ?)",
            (nome.strip(), unidade, now_brt())
        )
        return cur.lastrowid


def alternar_reutilizavel_tipo(tipo_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE item_tipo SET reutilizavel = 1 - COALESCE(reutilizavel, 0) WHERE id = ?",
            (tipo_id,)
        )


def alternar_unidade_tipo(tipo_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE item_tipo SET unidade = CASE WHEN unidade = 'm' THEN 'un' ELSE 'm' END WHERE id = ?",
            (tipo_id,)
        )


def buscar_dependencias_tipo(tipo_id: int) -> dict:
    with db() as conn:
        tipo = conn.execute("SELECT nome FROM item_tipo WHERE id = ?", (tipo_id,)).fetchone()
        patrimonios = conn.execute(
            "SELECT COUNT(*) AS n FROM item_master WHERE item_tipo_id = ?", (tipo_id,)
        ).fetchone()["n"]
        templates_rows = conn.execute(
            "SELECT DISTINCT kt.nome FROM kit_template_items ki "
            "JOIN kit_template kt ON kt.id = ki.kit_template_id "
            "WHERE ki.item_tipo_id = ?", (tipo_id,)
        ).fetchall()
        estoque_n = conn.execute(
            "SELECT COUNT(*) AS n FROM estoque WHERE item_tipo_id = ?", (tipo_id,)
        ).fetchone()["n"]
    return {
        "tipo_id": tipo_id,
        "tipo_nome": tipo["nome"] if tipo else "?",
        "patrimonios": patrimonios,
        "templates": [r["nome"] for r in templates_rows],
        "estoque": estoque_n,
    }


def deletar_tipo_cascade(tipo_id: int):
    with db() as conn:
        conn.execute("DELETE FROM scan_session_items WHERE item_tipo_id = ?", (tipo_id,))
        conn.execute(
            "DELETE FROM estoque_movimentos WHERE estoque_id IN "
            "(SELECT id FROM estoque WHERE item_tipo_id = ?)", (tipo_id,)
        )
        conn.execute("DELETE FROM item_master WHERE item_tipo_id = ?", (tipo_id,))
        conn.execute("DELETE FROM kit_template_items WHERE item_tipo_id = ?", (tipo_id,))
        conn.execute("DELETE FROM estoque WHERE item_tipo_id = ?", (tipo_id,))
        conn.execute("DELETE FROM item_tipo WHERE id = ?", (tipo_id,))


def deletar_tipo(tipo_id: int):
    with db() as conn:
        conn.execute("DELETE FROM item_tipo WHERE id = ?", (tipo_id,))


def toggle_tipo(tipo_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE item_tipo SET ativo = 1 - ativo WHERE id = ?", (tipo_id,)
        )


def importar_tipos_xlsx(conteudo: bytes) -> dict:
    """Lê um arquivo .xlsx e importa a primeira coluna (a partir da linha 2) como tipos de item.
    Retorna {'criados': N, 'ignorados': M} onde ignorados = duplicatas já existentes."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    ws = wb.active
    criados = 0
    ignorados = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        valor = row[0] if row else None
        if not valor:
            continue
        nome = str(valor).strip()
        if not nome:
            continue
        try:
            criar_tipo(nome)
            criados += 1
        except Exception:
            ignorados += 1
    wb.close()
    return {"criados": criados, "ignorados": ignorados}


# ── Patrimônios (item_master) ──────────────────────────────────────────────────

def listar_itens() -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT i.*, t.nome AS descricao, u.nome AS criado_por_nome "
            "FROM item_master i "
            "JOIN item_tipo t ON t.id = i.item_tipo_id "
            "LEFT JOIN users u ON u.id = i.criado_por "
            "ORDER BY t.nome, i.codigo_barra"
        ).fetchall()
    return [dict(r) for r in rows]


def buscar_item(codigo_barra: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT i.*, t.nome AS descricao, "
            "COALESCE(t.unidade, 'un') AS unidade, "
            "COALESCE(t.reutilizavel, 0) AS reutilizavel "
            "FROM item_master i "
            "JOIN item_tipo t ON t.id = i.item_tipo_id "
            "WHERE i.codigo_barra = ? AND i.ativo = 1",
            (codigo_barra,)
        ).fetchone()
    return dict(row) if row else None


def criar_item(codigo_barra: str, item_tipo_id: int, criado_por: int) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO item_master (codigo_barra, item_tipo_id, criado_por) "
            "VALUES (?, ?, ?)",
            (codigo_barra, item_tipo_id, criado_por)
        )
        return cur.lastrowid


def deletar_item(item_id: int):
    with db() as conn:
        conn.execute("DELETE FROM item_master WHERE id = ?", (item_id,))


def apagar_todos_itens():
    with db() as conn:
        conn.execute("DELETE FROM item_master")


def toggle_item(item_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE item_master SET ativo = 1 - ativo WHERE id = ?", (item_id,)
        )


def importar_bom_xlsx(conteudo: bytes, criado_por: int) -> dict:
    """Importa tipos e patrimônios a partir de um BOM Excel.

    Detecta automaticamente a linha de cabeçalho procurando por 'Description'.
    Colunas usadas: Code → item_master.codigo_barra, Description → item_tipo.nome.
    Rows without a description are skipped; rows without a code create only the tipo.
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    ws = wb.active

    # Detecta header row e índices de colunas
    header_row = None
    col_desc = col_code = None
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip().lower() if c else "" for c in row]
        if "description" in cells:
            header_row = True
            col_desc = next(i for i, c in enumerate(cells) if c == "description")
            # Code pode se chamar 'code', 'part number', 'código', etc.
            for label in ("code", "part number", "código", "codigo", "part no"):
                if label in cells:
                    col_code = next(i for i, c in enumerate(cells) if c == label)
                    break
            break

    if header_row is None:
        wb.close()
        return {"tipos_criados": 0, "itens_criados": 0, "ignorados": 0,
                "erro": "Cabeçalho 'Description' não encontrado na planilha."}

    tipos_criados = itens_criados = ignorados = 0

    with db() as conn:
        past_header = False
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip().lower() if c else "" for c in row]
            # Pula até depois do header
            if not past_header:
                if "description" in cells:
                    past_header = True
                continue

            desc = str(row[col_desc]).strip() if col_desc is not None and row[col_desc] else ""
            code = (str(row[col_code]).strip() if col_code is not None and row[col_code] else "")
            # Limpa values como "None" ou "no part number"
            if desc.lower() in ("none", "") or not desc:
                continue
            if code.lower() in ("none", "no part number", "n/a", ""):
                code = ""

            # Cria ou recupera o tipo
            existing_tipo = conn.execute(
                "SELECT id FROM item_tipo WHERE nome = ?", (desc,)
            ).fetchone()
            if existing_tipo:
                tipo_id = existing_tipo["id"]
                ignorados += 1
            else:
                cur = conn.execute(
                    "INSERT INTO item_tipo (nome, criado_em) VALUES (?, ?)",
                    (desc, now_brt())
                )
                tipo_id = cur.lastrowid
                tipos_criados += 1

            # Cria patrimônio se houver código
            if code:
                exists = conn.execute(
                    "SELECT 1 FROM item_master WHERE codigo_barra = ?", (code,)
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO item_master (codigo_barra, item_tipo_id, criado_por) "
                        "VALUES (?, ?, ?)",
                        (code, tipo_id, criado_por)
                    )
                    itens_criados += 1

    wb.close()
    return {"tipos_criados": tipos_criados, "itens_criados": itens_criados, "ignorados": ignorados}
