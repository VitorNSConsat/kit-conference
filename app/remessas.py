"""Remessa: juntar kits até fechar a quantidade combinada com o cliente.

O envio não é kit a kit — é por lote fechado ("mandar 90"). Até agora esse
número vivia num papel ou na cabeça de quem despacha, e a única forma de saber
quanto faltava era contar a lista de Em Trânsito na mão, todo dia.

Como funciona:

  • O operador ESCOLHE a remessa no começo da bipagem, e o kit entra nela
    quando é finalizado. A escolha fica guardada e vale pros próximos kits,
    até alguém trocar — quem monta trinta kits seguidos não escolhe trinta
    vezes.
  • Por isso pode haver VÁRIAS remessas abertas ao mesmo tempo: duas frentes
    de trabalho, dois clientes, dois lotes. Quem decide qual é o operador.
  • Kit montado SEM remessa escolhida ainda entra na conta quando vai pra Em
    Trânsito, desde que só exista uma aberta — é a rede de segurança pros
    kits que começaram antes de alguém escolher.
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


def listar_abertas() -> list[dict]:
    """Todas as remessas abertas — é a lista que o operador escolhe no começo
    da bipagem."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM remessa WHERE status = 'aberta' ORDER BY id DESC").fetchall()
    return [_com_contagem(dict(r)) for r in rows]


def aberta() -> dict | None:
    """A remessa aberta mais recente. Serve pra tela mostrar "a atual" quando
    não há escolha feita — não é mais "a única", porque agora pode haver
    várias."""
    abertas = listar_abertas()
    return abertas[0] if abertas else None


def unica_aberta() -> dict | None:
    """A remessa aberta SÓ quando não há dúvida (existe exatamente uma).

    Usada pela rede de segurança do trânsito: com duas abertas, escolher uma
    seria chutar em qual lote o kit do cliente entra — e errar isso é pior do
    que não contar."""
    abertas = listar_abertas()
    return abertas[0] if len(abertas) == 1 else None


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
    """Abre uma remessa — sem fechar as outras.

    Fechar a anterior era a regra quando só uma podia existir. Agora quem diz
    em qual lote o kit entra é o operador, na bipagem, então duas frentes de
    trabalho ao mesmo tempo deixaram de ser ambíguas e passaram a ser o caso
    normal."""
    alvo = _validar_alvo(alvo)
    nome = (nome or "").strip() or _proximo_nome()
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


def vincular_kit(kit_id: str, remessa_id: int | None) -> bool:
    """Coloca UM kit na remessa escolhida no começo da bipagem.

    É o caminho principal desde que a escolha passou pra bipagem: o kit entra
    na remessa no momento em que fica pronto, não quando é despachado. Assim o
    contador anda enquanto o galpão monta — que é o que "organizar os kits até
    fechar a quantidade" quer dizer."""
    if not kit_id or not remessa_id:
        return False
    r = listar_uma(int(remessa_id))
    if not r or r["status"] != "aberta":
        return False
    with db() as conn:
        if conn.execute("SELECT 1 FROM remessa_kit WHERE kit_id = ?", (kit_id,)).fetchone():
            return False
        conn.execute(
            "INSERT INTO remessa_kit (remessa_id, kit_id, entrou_em) VALUES (?, ?, ?)",
            (r["id"], kit_id, now_brt()))
    atual = listar_uma(r["id"])
    if atual and atual["completa"]:
        _fechar_e_seguir(r, "alvo alcançado")
    return True


def registrar_kits(kit_ids: list[str]) -> int:
    """Rede de segurança: kit que chega em Em Trânsito SEM remessa entra na
    aberta, desde que exista só uma.

    Antes este era o caminho principal. Virou reserva quando a escolha passou
    pra bipagem — serve pros kits montados antes disso e pra quem finalizou
    sem escolher. Com DUAS remessas abertas não faz nada: escolher uma seria
    chutar em qual lote o kit do cliente entra.

    Kit que já está em alguma remessa é ignorado — voltar e reenviar não conta
    duas vezes.
    """
    r = unica_aberta()
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


def candidatos(busca: str = "", limite: int = 300) -> list[dict]:
    """Kits que ainda não estão em remessa nenhuma.

    Existe pros kits que "já se passaram": os montados antes de a remessa
    existir, e os que saíram enquanto nenhuma estava aberta. Sem isto, o único
    jeito de acertar o lote seria refazer a bipagem."""
    termo = (busca or "").strip()
    sql = """
        SELECT kr.kit_id, kr.veiculo, kr.garagem, kr.modelo, kr.status_producao,
               kr.finalizado_em, kr.transito_em, kt.nome AS kit_nome, kt.cliente
        FROM kit_record kr
        JOIN kit_template kt ON kt.id = kr.kit_template_id
        WHERE kr.status = 'ativo' AND kt.tipo = 'kit'
          AND NOT EXISTS (SELECT 1 FROM remessa_kit rk WHERE rk.kit_id = kr.kit_id)
    """
    params: list = []
    if termo:
        sql += (" AND (sem_acento(kr.veiculo) LIKE sem_acento(?) "
                "   OR sem_acento(kt.cliente) LIKE sem_acento(?) "
                "   OR sem_acento(kt.nome) LIKE sem_acento(?))")
        params += [f"%{termo}%"] * 3
    sql += " ORDER BY kr.finalizado_em DESC LIMIT ?"
    params.append(limite)
    with db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def adicionar_kits(remessa_id: int, kit_ids: list[str]) -> dict:
    """Coloca à mão vários kits numa remessa ABERTA.

    Diferente de vincular_kit(), que trata um kit por vez vindo da bipagem:
    aqui a conta de "bateu o alvo" é feita UMA vez, no fim. Kit a kit, a
    remessa fecharia no meio da lista e o resto da seleção seria recusado
    sem o operador entender por quê."""
    r = listar_uma(remessa_id)
    if not r:
        raise ValueError("Remessa não encontrada.")
    if r["status"] != "aberta":
        raise ValueError(f"A remessa {r['nome']} está fechada. "
                         "Só dá pra acrescentar kit em remessa aberta.")
    entraram, ja_em_outra, cliente_errado = 0, 0, 0
    with db() as conn:
        for kit_id in [k for k in kit_ids if k]:
            if conn.execute("SELECT 1 FROM remessa_kit WHERE kit_id = ?", (kit_id,)).fetchone():
                ja_em_outra += 1
                continue
            if r["cliente"]:
                dono = conn.execute(
                    "SELECT kt.cliente FROM kit_record kr "
                    "JOIN kit_template kt ON kt.id = kr.kit_template_id "
                    "WHERE kr.kit_id = ?", (kit_id,)).fetchone()
                if not dono or (dono["cliente"] or "") != r["cliente"]:
                    cliente_errado += 1
                    continue
            conn.execute(
                "INSERT INTO remessa_kit (remessa_id, kit_id, entrou_em) VALUES (?, ?, ?)",
                (r["id"], kit_id, now_brt()))
            entraram += 1
    fechou = False
    if entraram:
        atual = listar_uma(r["id"])
        if atual and atual["completa"]:
            _fechar_e_seguir(r, "alvo alcançado ao acrescentar kits à mão")
            fechou = True
    return {"entraram": entraram, "ja_em_outra": ja_em_outra,
            "cliente_errado": cliente_errado, "fechou": fechou,
            "cliente": r["cliente"]}


def remover_kit(remessa_id: int, kit_id: str) -> bool:
    """Tira um kit da remessa — só de remessa ABERTA.

    Remessa fechada é histórico: mudar a conta de um lote já encerrado faria
    o número que foi passado pro cliente deixar de bater com o registro."""
    r = listar_uma(remessa_id)
    if not r or r["status"] != "aberta":
        raise ValueError("Só dá pra tirar kit de uma remessa aberta.")
    with db() as conn:
        cur = conn.execute("DELETE FROM remessa_kit WHERE remessa_id = ? AND kit_id = ?",
                           (remessa_id, kit_id))
    return cur.rowcount > 0


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
