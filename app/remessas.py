"""Remessa: juntar kits até fechar a quantidade combinada com o cliente.

O envio não é kit a kit — é por lote fechado ("mandar 90"). Até agora esse
número vivia num papel ou na cabeça de quem despacha, e a única forma de saber
quanto faltava era contar a lista de Em Trânsito na mão, todo dia.

Como funciona:

  • Uma remessa ABERTA por vez. Todo kit que entra em Em Trânsito entra nela —
    é o momento em que o kit sai do galpão, que é o que "enviar" quer dizer.
  • O ALVO pode mudar a qualquer momento (0/90 → 45/90 → 80/80). Mudar não
    mexe no que já foi contado: os kits continuam onde estão, só a meta muda.
  • Ao bater o alvo, a remessa FECHA e outra abre no lugar, do zero. É isso
    que deixa comparar uma remessa com a outra depois — sem o corte, o número
    só cresceria pra sempre e ninguém saberia onde uma acabou.

A remessa pode ser de um CLIENTE específico ou geral. Com cliente definido, só
kit daquele cliente entra — é o que permite ter "REDEMOB 45/90" sem que o kit
de outro cliente empurre a conta.
"""

from database import db, now_brt

ALVO_MIN, ALVO_MAX = 1, 100000


def aberta() -> dict | None:
    """A remessa que está recebendo kits agora. None = nenhuma aberta."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM remessa WHERE status = 'aberta' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    return _com_contagem(dict(row)) if row else None


def _com_contagem(r: dict) -> dict:
    with db() as conn:
        r["enviados"] = conn.execute(
            "SELECT COUNT(*) FROM remessa_kit WHERE remessa_id = ?", (r["id"],)).fetchone()[0]
    alvo = max(1, int(r["alvo"] or 1))
    r["faltam"] = max(0, alvo - r["enviados"])
    r["completa"] = r["enviados"] >= alvo
    # Passar de 100% acontece: o alvo pode ser reduzido depois que os kits já
    # entraram. A barra para em 100 pra não vazar da tela.
    r["percentual"] = min(100, round(r["enviados"] * 100 / alvo))
    r["rotulo"] = f"{r['enviados']}/{alvo}"
    return r


def abrir(nome: str, alvo, cliente: str = "", user_id: int | None = None) -> dict:
    """Abre uma remessa. Se já existe uma aberta, ela é fechada antes — duas
    abertas ao mesmo tempo tornariam ambíguo em qual o kit deve entrar."""
    alvo = _validar_alvo(alvo)
    nome = (nome or "").strip() or _proximo_nome()
    atual = aberta()
    if atual:
        fechar(atual["id"], motivo="substituída por uma remessa nova")
    with db() as conn:
        rid = conn.execute(
            "INSERT INTO remessa (nome, cliente, alvo, status, criada_em, criada_por) "
            "VALUES (?, ?, ?, 'aberta', ?, ?)",
            (nome, (cliente or "").strip(), alvo, now_brt(), user_id)).lastrowid
    return listar_uma(rid)


def _proximo_nome() -> str:
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM remessa").fetchone()[0]
    return f"Remessa {n + 1}"


def _validar_alvo(alvo) -> int:
    try:
        v = int(alvo)
    except (TypeError, ValueError):
        raise ValueError("Informe a quantidade da remessa (um número).")
    if v < ALVO_MIN:
        raise ValueError("A quantidade da remessa precisa ser pelo menos 1.")
    return min(v, ALVO_MAX)


def definir_alvo(remessa_id: int, alvo) -> dict:
    """Muda a meta no meio do caminho — é o "mudando o final a qualquer
    momento". Se a nova meta já foi alcançada, a remessa fecha na hora."""
    alvo = _validar_alvo(alvo)
    with db() as conn:
        conn.execute("UPDATE remessa SET alvo = ? WHERE id = ? AND status = 'aberta'",
                     (alvo, remessa_id))
    r = listar_uma(remessa_id)
    if r and r["status"] == "aberta" and r["completa"]:
        _fechar_e_seguir(r, "alvo alcançado ao ajustar a quantidade")
        return listar_uma(remessa_id)
    return r


def _fechar_e_seguir(r: dict, motivo: str) -> None:
    """Fecha a remessa cheia e já abre a próxima, com a mesma meta e o mesmo
    cliente.

    As DUAS formas de bater o alvo passam por aqui — o kit que chega e a meta
    que é reduzida pra baixo do que já entrou. Quando só a primeira abria a
    seguinte, baixar a meta deixava a operação sem remessa aberta, e os kits
    despachados depois não entravam em contagem nenhuma."""
    fechar(r["id"], motivo=motivo)
    abrir(_proximo_nome(), r["alvo"], r["cliente"], r.get("criada_por"))


def fechar(remessa_id: int, motivo: str = "") -> None:
    with db() as conn:
        conn.execute(
            "UPDATE remessa SET status = 'fechada', fechada_em = ?, "
            "observacao = ? WHERE id = ? AND status = 'aberta'",
            (now_brt(), motivo, remessa_id))


def registrar_kits(kit_ids: list[str]) -> int:
    """Coloca na remessa aberta os kits que acabaram de ir pra Em Trânsito.

    Chamado pela própria transição de estágio: se dependesse de alguém
    lembrar de apontar, a remessa nasceria desatualizada. Kit que já está em
    alguma remessa é ignorado — voltar e reenviar não conta duas vezes.

    Quando o alvo é atingido, fecha a remessa e abre a próxima com a MESMA
    meta e o mesmo cliente, pra a operação não parar esperando alguém criar.
    """
    r = aberta()
    if not r or not kit_ids:
        return 0
    entraram = 0
    with db() as conn:
        for kit_id in kit_ids:
            ja = conn.execute("SELECT 1 FROM remessa_kit WHERE kit_id = ?", (kit_id,)).fetchone()
            if ja:
                continue
            if r["cliente"]:
                dono = conn.execute(
                    "SELECT kt.cliente FROM kit_record kr "
                    "JOIN kit_template kt ON kt.id = kr.kit_template_id "
                    "WHERE kr.kit_id = ?", (kit_id,)).fetchone()
                if not dono or (dono["cliente"] or "") != r["cliente"]:
                    continue
            conn.execute(
                "INSERT INTO remessa_kit (remessa_id, kit_id, entrou_em) VALUES (?, ?, ?)",
                (r["id"], kit_id, now_brt()))
            entraram += 1
    if entraram:
        atual = listar_uma(r["id"])
        if atual and atual["completa"]:
            _fechar_e_seguir(r, "alvo alcançado")
    return entraram


def listar_uma(remessa_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM remessa WHERE id = ?", (remessa_id,)).fetchone()
    return _com_contagem(dict(row)) if row else None


def listar(limite: int = 60) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT r.*, u.nome AS criada_por_nome FROM remessa r "
            "LEFT JOIN users u ON u.id = r.criada_por "
            "ORDER BY r.status = 'fechada', r.id DESC LIMIT ?", (limite,)).fetchall()
    return [_com_contagem(dict(r)) for r in rows]


def kits_da_remessa(remessa_id: int) -> list[dict]:
    """Os kits daquela remessa, com o que a planilha precisa mostrar."""
    with db() as conn:
        rows = conn.execute("""
            SELECT rk.entrou_em, kr.kit_id, kr.veiculo, kr.garagem, kr.modelo,
                   kr.nota_fiscal, kr.nota_fiscal_data, kr.status_producao,
                   kr.finalizado_em, kr.transito_em,
                   kt.nome AS kit_nome, kt.cliente, u.nome AS operador_nome
            FROM remessa_kit rk
            JOIN kit_record kr ON kr.kit_id = rk.kit_id
            JOIN kit_template kt ON kt.id = kr.kit_template_id
            LEFT JOIN users u ON u.id = kr.operador_id
            WHERE rk.remessa_id = ?
            ORDER BY rk.entrou_em, kr.veiculo
        """, (remessa_id,)).fetchall()
    return [dict(r) for r in rows]
