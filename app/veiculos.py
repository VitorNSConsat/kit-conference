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
            (numero.strip(), cliente.strip(), garagem.strip().upper(), now_brt())
        )
        return cur.lastrowid


def atualizar(veiculo_id: int, numero: str, cliente: str, garagem: str):
    with db() as conn:
        conn.execute(
            "UPDATE veiculos SET numero=?, cliente=?, garagem=? WHERE id=?",
            (numero.strip(), cliente.strip(), garagem.strip().upper(), veiculo_id)
        )


def atualizar_garagem(veiculo_id: int, garagem: str):
    """Atualiza só a garagem — usado ao finalizar um kit vinculado a um
    veículo cadastrado, propagando o que foi digitado na bipagem (que na
    prática também guarda o modelo do veículo) para o cadastro dele."""
    garagem = (garagem or "").strip().upper()
    if not garagem:
        return
    with db() as conn:
        conn.execute("UPDATE veiculos SET garagem = ? WHERE id = ?", (garagem, veiculo_id))


def esta_ocupado(veiculo_id: int) -> bool:
    """Um veículo fica bloqueado pra nova bipagem assim que tem qualquer
    kit associado — em andamento ou já finalizado, não importa em que
    estágio da esteira está, mesmo já entregue ao cliente. Só libera com
    liberar() (manual); a liberação vale só até a próxima sessão nova ser
    criada pra ele (consumir_liberacao)."""
    with db() as conn:
        v = conn.execute(
            "SELECT liberado_em FROM veiculos WHERE id = ?", (veiculo_id,)
        ).fetchone()
        if not v:
            return False
        if v["liberado_em"]:
            return False
        tem_sessao_ativa = conn.execute(
            "SELECT 1 FROM scan_session WHERE veiculo_id = ? AND status = 'em_andamento' LIMIT 1",
            (veiculo_id,)
        ).fetchone()
        if tem_sessao_ativa:
            return True
        tem_kit = conn.execute(
            "SELECT 1 FROM kit_record WHERE veiculo_id = ? LIMIT 1", (veiculo_id,)
        ).fetchone()
        return tem_kit is not None


def liberar(veiculo_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE veiculos SET liberado_em = ? WHERE id = ?", (now_brt(), veiculo_id)
        )


def consumir_liberacao(veiculo_id: int) -> None:
    """Chamado ao criar uma sessão/kit novo pra esse veículo — a liberação
    manual vale só pra essa próxima atribuição, não pra sempre."""
    with db() as conn:
        conn.execute(
            "UPDATE veiculos SET liberado_em = NULL WHERE id = ?", (veiculo_id,)
        )


def desativar(veiculo_id: int):
    with db() as conn:
        conn.execute("UPDATE veiculos SET ativo=0 WHERE id=?", (veiculo_id,))


def reativar(veiculo_id: int):
    with db() as conn:
        conn.execute("UPDATE veiculos SET ativo=1 WHERE id=?", (veiculo_id,))


def deletar(veiculo_id: int):
    """Exclui o veículo preservando o histórico, que é o que a tela promete
    ao pedir a confirmação.

    kit_record e scan_session referenciam veiculos(id), então o DELETE
    direto batia na FOREIGN KEY e derrubava a requisição com erro 500. Em
    vez de bloquear a exclusão (como fazemos com usuário), aqui dá pra
    preservar tudo: o número do veículo já vive também num campo de texto
    nas duas tabelas, então basta garantir que esse texto esteja preenchido
    antes de soltar o vínculo. Os kits continuam existindo e continuam
    mostrando pra qual veículo foram."""
    with db() as conn:
        # Garante o número em texto antes de perder a referência — se o
        # texto já estiver preenchido, COALESCE/NULLIF mantém o que havia.
        conn.execute(
            "UPDATE kit_record SET veiculo = COALESCE(NULLIF(veiculo, ''), "
            "  (SELECT numero FROM veiculos WHERE id = ?)) "
            "WHERE veiculo_id = ?",
            (veiculo_id, veiculo_id)
        )
        conn.execute(
            "UPDATE scan_session SET veiculo = COALESCE(NULLIF(veiculo, ''), "
            "  (SELECT numero FROM veiculos WHERE id = ?)) "
            "WHERE veiculo_id = ?",
            (veiculo_id, veiculo_id)
        )
        conn.execute("UPDATE kit_record SET veiculo_id = NULL WHERE veiculo_id = ?", (veiculo_id,))
        conn.execute("UPDATE scan_session SET veiculo_id = NULL WHERE veiculo_id = ?", (veiculo_id,))
        conn.execute("DELETE FROM veiculos WHERE id=?", (veiculo_id,))


def importar_excel(file_bytes: bytes) -> dict:
    import openpyxl, io, sqlite3
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
            # Cria o cliente na MESMA conexão/transação — chamar clientes.criar()
            # aqui abriria uma segunda conexão SQLite enquanto esta ainda está
            # com uma transação aberta, travando o banco ("database is locked").
            try:
                conn.execute(
                    "INSERT INTO clientes (nome, criado_em) VALUES (?, ?)",
                    (cliente, now_brt())
                )
            except sqlite3.IntegrityError:
                pass  # cliente já existe
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


def kits_para_vincular(veiculo_id: int, limite: int = 100) -> list[dict]:
    """Kits finalizados que podem ser vinculados a este veículo: os que não
    têm veículo nenhum e os que estão em OUTRO veículo (troca). Cada linha
    traz `veiculo_atual` preenchido quando já há vínculo — é o que faz a
    tela exigir motivo antes de trocar. Limitado aos mais recentes:
    atribuição manual é correção pontual, não varredura de histórico."""
    with db() as conn:
        rows = conn.execute("""
            SELECT kr.kit_id, kt.nome AS kit_nome, kt.cliente,
                   kr.finalizado_em,
                   kr.veiculo_id AS veiculo_id_atual,
                   COALESCE(v.numero, kr.veiculo, '') AS veiculo_atual
            FROM kit_record kr
            JOIN kit_template kt ON kt.id = kr.kit_template_id
            LEFT JOIN veiculos v ON v.id = kr.veiculo_id
            WHERE kr.veiculo_id IS NULL OR kr.veiculo_id != ?
            ORDER BY kr.finalizado_em DESC
            LIMIT ?
        """, (veiculo_id, limite)).fetchall()
    return [dict(r) for r in rows]


def clientes_disponiveis() -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT cliente FROM kit_template WHERE ativo=1 ORDER BY cliente"
        ).fetchall()
    return [r[0] for r in rows]
