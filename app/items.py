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
    """Retorna os tipos presentes no template disponíveis para classificação manual
    de um código desconhecido — excluindo tipos com código fixo (têm fluxo próprio)
    e mostrando apenas tipos marcados como "Item de Patrimônio" (controle_externo=1).
    Só tipos individualmente rastreados por patrimônio devem ser opções nessa tela;
    tipos não marcados ficam ocultos dela."""
    with db() as conn:
        rows = conn.execute(
            "SELECT it.id, it.nome FROM item_tipo it "
            "JOIN kit_template_items ki ON ki.item_tipo_id = it.id "
            "WHERE ki.kit_template_id = ? AND it.ativo = 1 "
            "AND (it.codigo_fixo IS NULL OR it.codigo_fixo = '') "
            "AND COALESCE(it.controle_externo, 0) = 1 "
            "ORDER BY it.nome",
            (template_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def buscar_tipo_por_codigo_fixo(codigo: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id, nome, reutilizavel FROM item_tipo WHERE codigo_fixo = ? AND ativo = 1",
            (codigo,)
        ).fetchone()
    return dict(row) if row else None


def definir_codigo_fixo(tipo_id: int, codigo: str | None):
    valor = codigo.strip() if codigo and codigo.strip() else None
    with db() as conn:
        conn.execute(
            "UPDATE item_tipo SET codigo_fixo = ? WHERE id = ?", (valor, tipo_id)
        )


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


def alternar_controle_externo(tipo_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE item_tipo SET controle_externo = 1 - COALESCE(controle_externo, 0) WHERE id = ?",
            (tipo_id,)
        )


def alternar_requer_serial(tipo_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE item_tipo SET requer_serial = 1 - COALESCE(requer_serial, 0) WHERE id = ?",
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


def renomear_tipo(tipo_id: int, novo_nome: str):
    with db() as conn:
        conn.execute("UPDATE item_tipo SET nome = ? WHERE id = ?", (novo_nome.strip(), tipo_id))


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

# Por que um patrimônio aparece sem veículo. Não é erro por si só — a
# maioria dos casos é situação normal (item novo, kit ainda em montagem).
SITUACOES = {
    "ok": "",
    "nunca_bipado": "Cadastrado mas nunca bipado em nenhum kit",
    "em_bipagem": "Está numa bipagem em andamento — ganha veículo ao finalizar",
    "kit_sem_veiculo": "Bipado num kit que foi finalizado sem veículo definido",
    "kit_removido": "O kit onde foi bipado não existe mais (excluído)",
}


def listar_itens(veiculo_id: int | None = None, situacao: str = "") -> list:
    """Patrimônios cadastrados, com o veículo, serial e operador do kit mais
    recente em que cada um foi bipado (o kit 'ativo' mais novo, então pra
    item reutilizável reflete a atribuição atual, não o histórico inteiro).

    Cada item traz também `situacao`, que explica por que está sem veículo
    quando for o caso — a lista sozinha não distinguia "nunca foi bipado"
    de "o kit foi finalizado sem veículo", que pedem ações bem diferentes.

    veiculo_id filtra os itens atribuídos a esse veículo; situacao filtra
    por um dos códigos de SITUACOES."""
    with db() as conn:
        rows = conn.execute("""
            WITH ult_kit AS (
                SELECT si.codigo_barra, si.serial_number,
                       kr.kit_id, kr.veiculo_id, kr.veiculo, kr.garagem,
                       kr.finalizado_em, kr.operador_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY si.codigo_barra
                           ORDER BY kr.finalizado_em DESC
                       ) AS rn
                FROM scan_session_items si
                JOIN scan_session ss ON ss.id = si.sessao_id
                JOIN kit_record kr ON kr.sessao_id = ss.id
                WHERE kr.status = 'ativo'
            )
            SELECT i.*, t.nome AS descricao, u.nome AS criado_por_nome,
                   k.veiculo_id AS veiculo_id_atual,
                   COALESCE(v.numero, k.veiculo) AS veiculo_atual,
                   k.kit_id  AS kit_id_atual,
                   k.garagem AS garagem_atual,
                   k.serial_number AS serial_atual,
                   k.finalizado_em AS bipado_em_atual,
                   op.nome AS operador_atual,
                   (SELECT COUNT(*) FROM scan_session_items s2
                     WHERE s2.codigo_barra = i.codigo_barra) AS total_bipagens,
                   (SELECT ss2.status FROM scan_session_items s2
                     JOIN scan_session ss2 ON ss2.id = s2.sessao_id
                     WHERE s2.codigo_barra = i.codigo_barra
                     ORDER BY s2.id DESC LIMIT 1) AS status_ultima_sessao
            FROM item_master i
            JOIN item_tipo t ON t.id = i.item_tipo_id
            LEFT JOIN users u ON u.id = i.criado_por
            LEFT JOIN ult_kit k ON k.codigo_barra = i.codigo_barra AND k.rn = 1
            LEFT JOIN veiculos v ON v.id = k.veiculo_id
            LEFT JOIN users op ON op.id = k.operador_id
            ORDER BY t.nome, i.codigo_barra
        """).fetchall()

    itens = []
    for r in rows:
        d = dict(r)
        if d["veiculo_atual"]:
            d["situacao"] = "ok"
        elif not d["total_bipagens"]:
            d["situacao"] = "nunca_bipado"
        elif d["status_ultima_sessao"] == "em_andamento":
            d["situacao"] = "em_bipagem"
        elif d["kit_id_atual"]:
            d["situacao"] = "kit_sem_veiculo"
        else:
            d["situacao"] = "kit_removido"
        d["situacao_texto"] = SITUACOES[d["situacao"]]
        itens.append(d)

    if veiculo_id:
        itens = [i for i in itens if i["veiculo_id_atual"] == veiculo_id]
    if situacao in SITUACOES:
        itens = [i for i in itens if i["situacao"] == situacao]
    return itens


def historico_patrimonio(codigo_barra: str) -> list[dict]:
    """Toda vez que este código foi bipado: quando, em que sessão/kit, por
    qual operador e pra qual veículo. Responde 'onde foi bipado?' sem
    depender de o kit ainda ter veículo."""
    with db() as conn:
        rows = conn.execute("""
            SELECT si.id AS si_id, si.bipado_em, si.serial_number, si.observacao,
                   si.sessao_id, ss.status AS sessao_status,
                   kt.nome AS kit_nome, kt.cliente,
                   kr.kit_id, kr.status AS kit_status,
                   COALESCE(v.numero, kr.veiculo, ss.veiculo, '') AS veiculo,
                   COALESCE(kr.garagem, ss.garagem, '') AS garagem,
                   COALESCE(opi.nome, ops.nome) AS operador_nome
            FROM scan_session_items si
            JOIN scan_session ss ON ss.id = si.sessao_id
            JOIN kit_template kt ON kt.id = ss.kit_template_id
            LEFT JOIN kit_record kr ON kr.sessao_id = ss.id
            LEFT JOIN veiculos v ON v.id = kr.veiculo_id
            LEFT JOIN users opi ON opi.id = si.operador_id
            LEFT JOIN users ops ON ops.id = ss.operador_id
            WHERE si.codigo_barra = ?
            ORDER BY si.bipado_em DESC, si.id DESC
        """, (codigo_barra,)).fetchall()
    return [dict(r) for r in rows]


def bipados_na_mesma_sessao(sessao_id: int, codigo_barra: str) -> list[dict]:
    """O que mais foi bipado na mesma sessão, em ordem cronológica — dá pra
    ver o que veio logo antes e logo depois deste item. Marca a linha do
    próprio item pra facilitar achar o ponto na sequência."""
    with db() as conn:
        rows = conn.execute("""
            SELECT si.codigo_barra, si.bipado_em, si.serial_number, si.quantidade,
                   it.nome AS descricao, u.nome AS operador_nome
            FROM scan_session_items si
            JOIN item_tipo it ON it.id = si.item_tipo_id
            LEFT JOIN users u ON u.id = si.operador_id
            WHERE si.sessao_id = ?
            ORDER BY si.bipado_em, si.id
        """, (sessao_id,)).fetchall()
    itens = []
    for r in rows:
        d = dict(r)
        d["e_o_item"] = d["codigo_barra"] == codigo_barra
        itens.append(d)
    return itens


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
            "INSERT INTO item_master (codigo_barra, item_tipo_id, criado_por, criado_em) "
            "VALUES (?, ?, ?, ?)",
            (codigo_barra, item_tipo_id, criado_por, now_brt())
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
                        "INSERT INTO item_master (codigo_barra, item_tipo_id, criado_por, criado_em) "
                        "VALUES (?, ?, ?, ?)",
                        (code, tipo_id, criado_por, now_brt())
                    )
                    itens_criados += 1

    wb.close()
    return {"tipos_criados": tipos_criados, "itens_criados": itens_criados, "ignorados": ignorados}
