from database import db, now_brt


def listar(cliente: str | None = None, ativo: bool = True) -> list[dict]:
    sql = """
        SELECT v.*,
               COUNT(kr.kit_id) AS total_kits,
               MAX(kr.finalizado_em) AS ultimo_kit_em
        FROM veiculos v
        LEFT JOIN kit_record kr ON kr.veiculo_id = v.id
        WHERE v.ativo = ?
    """
    params: list = [1 if ativo else 0]
    if cliente:
        sql += " AND v.cliente = ?"
        params.append(cliente)
    sql += " GROUP BY v.id ORDER BY v.cliente, v.numero"
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def buscar(veiculo_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM veiculos WHERE id = ?", (veiculo_id,)
        ).fetchone()
    return dict(row) if row else None


def criar(numero: str, cliente: str, garagem: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO veiculos (numero, cliente, garagem, criado_em) VALUES (?, ?, ?, ?)",
            (numero.strip(), cliente.strip(), garagem.strip(), now_brt())
        )
        return cur.lastrowid


def atualizar(veiculo_id: int, numero: str, cliente: str, garagem: str):
    with db() as conn:
        conn.execute(
            "UPDATE veiculos SET numero=?, cliente=?, garagem=? WHERE id=?",
            (numero.strip(), cliente.strip(), garagem.strip(), veiculo_id)
        )


def desativar(veiculo_id: int):
    with db() as conn:
        conn.execute("UPDATE veiculos SET ativo=0 WHERE id=?", (veiculo_id,))


def importar_excel(file_bytes: bytes) -> dict:
    import openpyxl, io
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
    ws = wb.active
    headers = [str(c.value or "").strip().lower() for c in next(ws.iter_rows(min_row=1, max_row=1))]
    try:
        col_num = next(i for i, h in enumerate(headers) if "número" in h or "numero" in h or "veículo" in h or "veiculo" in h)
        col_cli = next(i for i, h in enumerate(headers) if "cliente" in h)
    except StopIteration:
        return {"inseridos": 0, "ignorados": 0, "erros": ["Cabeçalhos não encontrados. Use 'Número do Veículo' e 'Cliente'."]}

    inseridos = ignorados = 0
    erros: list[str] = []
    with db() as conn:
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
            if len(row) <= max(col_num, col_cli):
                ignorados += 1
                continue
            numero = str(row[col_num] or "").strip()
            cliente = str(row[col_cli] or "").strip()
            if not numero or not cliente:
                ignorados += 1
                continue
            existe = conn.execute(
                "SELECT id FROM veiculos WHERE numero=? AND cliente=? AND ativo=1",
                (numero, cliente)
            ).fetchone()
            if existe:
                ignorados += 1
            else:
                conn.execute(
                    "INSERT INTO veiculos (numero, cliente, garagem, criado_em) VALUES (?, ?, '', ?)",
                    (numero, cliente, now_brt())
                )
                inseridos += 1
    return {"inseridos": inseridos, "ignorados": ignorados, "erros": erros}


def historico_kits(veiculo_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute("""
            SELECT kr.kit_id, kt.nome AS kit_nome, kt.cliente,
                   kr.veiculo AS veiculo_texto, kr.garagem,
                   kr.finalizado_em, u.nome AS operador_nome,
                   CASE WHEN COUNT(kv.id) > 0 THEN 1 ELSE 0 END AS tem_verificacao
            FROM kit_record kr
            JOIN kit_template kt ON kt.id = kr.kit_template_id
            JOIN users u ON u.id = kr.operador_id
            LEFT JOIN kit_validacoes kv ON kv.kit_id = kr.kit_id
            WHERE kr.veiculo_id = ?
            GROUP BY kr.kit_id
            ORDER BY kr.finalizado_em DESC
        """, (veiculo_id,)).fetchall()
    return [dict(r) for r in rows]


def clientes_disponiveis() -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT cliente FROM kit_template WHERE ativo=1 ORDER BY cliente"
        ).fetchall()
    return [r[0] for r in rows]
