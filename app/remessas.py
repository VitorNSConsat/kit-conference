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


def _situacao(r: dict) -> str:
    """Como a tela chama o status — texto unico, pra mensagem de erro e
    listagem nao divergirem."""
    return {"aberta": "aberta", "fechada": "fechada",
            "arquivada": "arquivada"}.get(r["status"], r["status"])


def editar(remessa_id: int, nome: str = None, alvo=None, cliente: str = None) -> dict:
    """Corrige os dados da remessa em QUALQUER status.

    Corrigir o nome ou a quantidade de um lote ja fechado e trabalho
    administrativo legitimo — e nada disso mexe em producao, bipagem,
    patrimonio ou estoque. So o CLIENTE tem trava: mudar o dono de uma
    remessa que ja tem item de outro cliente deixaria o lote incoerente.
    """
    r = listar_uma(remessa_id)
    if not r:
        raise ValueError("Remessa nao encontrada.")
    campos, valores = [], []
    if nome is not None and nome.strip():
        campos.append("nome = ?")
        valores.append(nome.strip())
    if alvo is not None and str(alvo).strip() != "":
        campos.append("alvo = ?")
        valores.append(_validar_alvo(alvo))
    if cliente is not None and (cliente or "").strip() != (r["cliente"] or ""):
        novo_cli = (cliente or "").strip()
        if novo_cli:
            with db() as conn:
                fora = conn.execute(
                    "SELECT COUNT(*) FROM remessa_kit rk "
                    "LEFT JOIN kit_record kr ON kr.kit_id = rk.kit_id "
                    "LEFT JOIN kit_template kt ON kt.id = kr.kit_template_id "
                    "LEFT JOIN veiculos v ON v.id = rk.veiculo_id "
                    "WHERE rk.remessa_id = ? "
                    "  AND COALESCE(kt.cliente, v.cliente, '') <> ?",
                    (remessa_id, novo_cli)).fetchone()[0]
            if fora:
                raise ValueError(
                    f"Esta remessa ja tem {fora} veiculo(s) que nao sao de {novo_cli}. "
                    "Tire-os antes de mudar o cliente, ou deixe a remessa sem cliente.")
        campos.append("cliente = ?")
        valores.append(novo_cli)
    if not campos:
        return r
    valores.append(remessa_id)
    with db() as conn:
        conn.execute(f"UPDATE remessa SET {', '.join(campos)} WHERE id = ?", valores)
    atual = listar_uma(remessa_id)
    # Baixar o alvo pra menos do que ja entrou fecha, igual em definir_alvo().
    if atual and atual["status"] == "aberta" and atual["completa"]:
        _fechar_e_seguir(atual, "alvo alcancado ao editar a remessa")
        return listar_uma(remessa_id)
    return atual


def reabrir(remessa_id: int) -> dict:
    """Volta uma remessa fechada/arquivada pra aberta — o caminho pra corrigir
    o que ja foi encerrado sem precisar apagar nada."""
    r = listar_uma(remessa_id)
    if not r:
        raise ValueError("Remessa nao encontrada.")
    with db() as conn:
        conn.execute("UPDATE remessa SET status = 'aberta', fechada_em = NULL, "
                     "arquivada_em = NULL, observacao = ? WHERE id = ?",
                     ("reaberta para correcao", remessa_id))
    return listar_uma(remessa_id)


def arquivar(remessa_id: int, motivo: str = "") -> dict:
    """Tira a remessa do caminho SEM apagar nada.

    E a alternativa a exclusao quando ha historico: o lote some das listas de
    trabalho, mas os kits continuam sabendo em que remessa foram, e a planilha
    daquele envio continua existindo. Apagar levaria essa memoria junto."""
    r = listar_uma(remessa_id)
    if not r:
        raise ValueError("Remessa nao encontrada.")
    with db() as conn:
        conn.execute("UPDATE remessa SET status = 'arquivada', arquivada_em = ?, "
                     "observacao = ? WHERE id = ?",
                     (now_brt(), (motivo or "").strip() or "arquivada", remessa_id))
    return listar_uma(remessa_id)


def dependencias(remessa_id: int) -> dict:
    """O que existe amarrado a esta remessa — o que a tela mostra ANTES de
    deixar excluir."""
    with db() as conn:
        linhas = conn.execute(
            "SELECT COUNT(*) AS itens, "
            "       SUM(CASE WHEN kit_id IS NOT NULL THEN 1 ELSE 0 END) AS kits, "
            "       SUM(CASE WHEN kit_id IS NULL THEN 1 ELSE 0 END) AS sem_kit "
            "FROM remessa_kit WHERE remessa_id = ?", (remessa_id,)).fetchone()
        bipagens = conn.execute(
            "SELECT COUNT(*) FROM scan_session WHERE remessa_id = ?", (remessa_id,)).fetchone()[0]
        enviados = conn.execute(
            "SELECT COUNT(*) FROM remessa_kit rk JOIN kit_record kr ON kr.kit_id = rk.kit_id "
            "WHERE rk.remessa_id = ? AND kr.status_producao IN "
            "      ('transito','cliente_instalando','cliente_concluido')",
            (remessa_id,)).fetchone()[0]
    d = dict(linhas)
    d["itens"] = d["itens"] or 0
    d["kits"] = d["kits"] or 0
    d["sem_kit"] = d["sem_kit"] or 0
    d["bipagens"] = bipagens
    d["enviados"] = enviados
    d["vazia"] = d["itens"] == 0 and bipagens == 0
    return d


def excluir(remessa_id: int) -> dict:
    """Apaga a remessa — SO quando ela nao tem nada amarrado.

    Com itens ou bipagens, recusa e manda arquivar: apagar a remessa nao pode
    levar junto o registro de que aqueles kits foram enviados. Nada de
    producao, bipagem, patrimonio ou estoque e tocado em nenhum dos casos."""
    r = listar_uma(remessa_id)
    if not r:
        raise ValueError("Remessa nao encontrada.")
    dep = dependencias(remessa_id)
    if not dep["vazia"]:
        partes = []
        if dep["itens"]:
            partes.append(f"{dep['itens']} veiculo(s)")
        if dep["enviados"]:
            partes.append(f"{dep['enviados']} ja enviado(s)")
        if dep["bipagens"]:
            partes.append(f"{dep['bipagens']} bipagem(ns) apontando pra ela")
        raise ValueError(
            f"{r['nome']} tem " + ", ".join(partes) + ". Apagar levaria junto o registro "
            "de que esses kits foram enviados. Use ARQUIVAR: some das listas e o "
            "historico fica.")
    with db() as conn:
        conn.execute("DELETE FROM remessa WHERE id = ?", (remessa_id,))
    return {"nome": r["nome"]}


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
        dono = conn.execute("SELECT veiculo_id FROM kit_record WHERE kit_id = ?",
                            (kit_id,)).fetchone()
        veiculo_id = dono["veiculo_id"] if dono else None
        # O veiculo ja podia estar na remessa como "a produzir": a bipagem
        # PREENCHE aquela linha em vez de criar outra. Sem isso, o mesmo
        # veiculo contaria duas vezes no alvo.
        linha = conn.execute(
            "SELECT id, remessa_id FROM remessa_kit WHERE veiculo_id IS NOT NULL "
            "AND veiculo_id = ?", (veiculo_id,)).fetchone() if veiculo_id else None
        if linha:
            conn.execute("UPDATE remessa_kit SET kit_id = ?, remessa_id = ? WHERE id = ?",
                         (kit_id, r["id"], linha["id"]))
        else:
            conn.execute(
                "INSERT INTO remessa_kit (remessa_id, kit_id, veiculo_id, entrou_em) "
                "VALUES (?, ?, ?, ?)", (r["id"], kit_id, veiculo_id, now_brt()))
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


# Rotulo de cada etapa, na ordem em que o veiculo as percorre. E o mesmo
# vocabulario da Producao — a remessa nao inventa nome de estagio.
ETAPAS = (
    ("a_produzir",   "A produzir"),
    ("em_producao",  "Em producao"),
    ("produzido",    "Produzido"),
    ("transito",     "Em transito"),
    ("no_cliente",   "No cliente"),
)


def candidatos(busca: str = "", etapas: list = None,
               data_ini: str = "", data_fim: str = "", limite: int = 500) -> list:
    """O que ainda nao esta em remessa nenhuma — em QUALQUER etapa.

    Sao tres origens diferentes, e a remessa precisa das tres:

      * kit montado (kit_record) — produzido, em transito, no cliente;
      * bipagem em andamento (scan_session) — "em producao", ainda sem kit;
      * veiculo pronto pra bipar, sem bipagem nenhuma — "a produzir".

    As duas ultimas nao tem kit_id: quem organiza o envio precisa fechar o
    lote ANTES de o galpao montar, e esperar o kit existir era tarde demais.
    Elas entram na remessa pelo VEICULO, e ganham o kit_id quando a bipagem
    termina.

    `data_ini`/`data_fim` filtram pela DATA DE IMPORTACAO do veiculo
    (veiculos.criado_em) — e o que permite "os 37 que entraram no dia 25".
    """
    termo = (busca or "").strip()
    validas = dict(ETAPAS)
    etapas = [e for e in (etapas or []) if e in validas] or list(validas)
    itens = []

    with db() as conn:
        # 1. Kits montados (produzido / transito / cliente).
        mapa_kit = {"produzido": ("produzido",), "transito": ("transito",),
                    "no_cliente": ("cliente_instalando", "cliente_concluido")}
        estagios = [e for chave in etapas for e in mapa_kit.get(chave, ())]
        if estagios:
            marcas = ",".join("?" * len(estagios))
            sql = (
                "SELECT kr.kit_id, kr.veiculo_id, kr.veiculo, kr.garagem, kr.modelo, "
                "       kr.status_producao, kr.finalizado_em, kr.transito_em, "
                "       kt.nome AS kit_nome, kt.cliente, v.criado_em AS importado_em "
                "FROM kit_record kr "
                "JOIN kit_template kt ON kt.id = kr.kit_template_id "
                "LEFT JOIN veiculos v ON v.id = kr.veiculo_id "
                "WHERE kr.status = 'ativo' AND kt.tipo = 'kit' "
                f"  AND kr.status_producao IN ({marcas}) "
                "  AND NOT EXISTS (SELECT 1 FROM remessa_kit rk WHERE rk.kit_id = kr.kit_id) "
                "  AND (kr.veiculo_id IS NULL OR NOT EXISTS ("
                "        SELECT 1 FROM remessa_kit rk2 WHERE rk2.veiculo_id = kr.veiculo_id))")
            for r in conn.execute(sql, estagios).fetchall():
                d = dict(r)
                d["etapa"] = ("no_cliente" if (d["status_producao"] or "").startswith("cliente")
                              else d["status_producao"])
                d["ref"] = "kit:" + d["kit_id"]
                itens.append(d)

        # 2. Bipagem em andamento — ainda nao virou kit.
        if "em_producao" in etapas:
            for r in conn.execute(
                "SELECT ss.veiculo_id, COALESCE(v.numero, ss.veiculo) AS veiculo, "
                "       COALESCE(v.garagem, ss.garagem) AS garagem, "
                "       COALESCE(NULLIF(TRIM(v.modelo), ''), ss.modelo) AS modelo, "
                "       kt.nome AS kit_nome, kt.cliente, ss.iniciado_em, "
                "       v.criado_em AS importado_em "
                "FROM scan_session ss "
                "JOIN kit_template kt ON kt.id = ss.kit_template_id "
                "LEFT JOIN veiculos v ON v.id = ss.veiculo_id "
                "WHERE ss.status = 'em_andamento' AND kt.tipo = 'kit' "
                "  AND ss.veiculo_id IS NOT NULL "
                "  AND NOT EXISTS (SELECT 1 FROM remessa_kit rk "
                "                   WHERE rk.veiculo_id = ss.veiculo_id)").fetchall():
                d = dict(r)
                d.update(kit_id=None, status_producao="em_producao", etapa="em_producao",
                         finalizado_em=None, transito_em=None,
                         ref="veic:%d" % d["veiculo_id"])
                itens.append(d)

        # 3. Veiculo pronto pra bipar, sem bipagem nenhuma. Mesmas condicoes
        #    de "Kits a produzir" na Producao: cliente, garagem e modelo.
        if "a_produzir" in etapas:
            for r in conn.execute(
                "SELECT v.id AS veiculo_id, v.numero AS veiculo, v.garagem, v.modelo, "
                "       v.cliente, v.criado_em AS importado_em, kt.nome AS kit_nome "
                "FROM veiculos v "
                "JOIN kit_template kt ON kt.id = ("
                "    SELECT k2.id FROM kit_template k2 "
                "     WHERE LOWER(TRIM(k2.nome)) = LOWER(TRIM(v.modelo)) "
                "       AND k2.ativo = 1 AND k2.tipo = 'kit' "
                "     ORDER BY k2.versao DESC, k2.id DESC LIMIT 1) "
                "WHERE v.ativo = 1 "
                "  AND TRIM(COALESCE(v.cliente, '')) != '' "
                "  AND TRIM(COALESCE(v.garagem, '')) != '' "
                "  AND TRIM(COALESCE(v.modelo,  '')) != '' "
                "  AND NOT EXISTS (SELECT 1 FROM remessa_kit rk WHERE rk.veiculo_id = v.id) "
                "  AND NOT EXISTS (SELECT 1 FROM kit_record kr "
                "                   WHERE kr.veiculo_id = v.id AND kr.status = 'ativo') "
                "  AND NOT EXISTS (SELECT 1 FROM scan_session ss "
                "                   WHERE ss.veiculo_id = v.id AND ss.status = 'em_andamento')").fetchall():
                d = dict(r)
                d.update(kit_id=None, status_producao="a_produzir", etapa="a_produzir",
                         finalizado_em=None, transito_em=None,
                         ref="veic:%d" % d["veiculo_id"])
                itens.append(d)

    if termo:
        alvo = termo.lower()
        itens = [d for d in itens if alvo in " ".join(
            str(d.get(c) or "") for c in ("veiculo", "cliente", "kit_nome", "modelo")).lower()]
    if data_ini:
        itens = [d for d in itens if (d.get("importado_em") or "")[:10] >= data_ini]
    if data_fim:
        itens = [d for d in itens if (d.get("importado_em") or "")[:10] <= data_fim]

    ordem = {c: i for i, (c, _r) in enumerate(ETAPAS)}
    itens.sort(key=lambda d: (ordem.get(d["etapa"], 9), d.get("veiculo") or ""))
    return itens[:limite]


def _dono_do_item(conn, kit_id, veiculo_id):
    """De que CLIENTE e este item — pra remessa de cliente unico recusar os
    outros. Kit sabe pelo template; veiculo, pelo cadastro."""
    if kit_id:
        r = conn.execute(
            "SELECT kt.cliente FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "WHERE kr.kit_id = ?", (kit_id,)).fetchone()
        return (r["cliente"] or "") if r else None
    r = conn.execute("SELECT cliente FROM veiculos WHERE id = ?", (veiculo_id,)).fetchone()
    return (r["cliente"] or "") if r else None


def _ler_ref(ref: str):
    """"kit:K-123" ou "veic:45" -> (kit_id, veiculo_id).

    Uma referencia so, em vez de duas listas no formulario: a tela mistura
    etapas na mesma tabela, e duas listas paralelas se desencontrariam na
    primeira mudanca de ordem."""
    ref = (ref or "").strip()
    if ref.startswith("kit:"):
        return ref[4:], None
    if ref.startswith("veic:"):
        try:
            return None, int(ref[5:])
        except ValueError:
            return None, None
    return (ref or None), None      # compatibilidade: kit_id puro


def adicionar_itens(remessa_id: int, refs: list, user_id: int = None) -> dict:
    """Coloca a mao varios itens numa remessa — kits prontos e/ou veiculos
    ainda sem kit.

    A conta de "bateu o alvo" e feita UMA vez, no fim. Item a item, a remessa
    fecharia no meio da lista e o resto da selecao seria recusado sem o
    operador entender por que."""
    r = listar_uma(remessa_id)
    if not r:
        raise ValueError("Remessa nao encontrada.")
    if r["status"] != "aberta":
        raise ValueError(f"A remessa {r['nome']} esta {_situacao(r)}. "
                         "So da pra acrescentar em remessa aberta.")
    entraram, ja_em_outra, cliente_errado, invalidos = 0, 0, 0, 0
    with db() as conn:
        for ref in refs:
            kit_id, veiculo_id = _ler_ref(ref)
            if not kit_id and not veiculo_id:
                invalidos += 1
                continue
            # Ja esta em alguma remessa? Vale pelos DOIS lados: o kit e o
            # veiculo dele. Sem isso, dava pra por o mesmo veiculo duas vezes
            # (uma como kit, outra como veiculo).
            if veiculo_id is None and kit_id:
                dono_v = conn.execute(
                    "SELECT veiculo_id FROM kit_record WHERE kit_id = ?", (kit_id,)).fetchone()
                veiculo_id = dono_v["veiculo_id"] if dono_v else None
            ja = conn.execute(
                "SELECT 1 FROM remessa_kit WHERE (kit_id IS NOT NULL AND kit_id = ?) "
                "   OR (veiculo_id IS NOT NULL AND veiculo_id = ?)",
                (kit_id, veiculo_id)).fetchone()
            if ja:
                ja_em_outra += 1
                continue
            if r["cliente"]:
                dono = _dono_do_item(conn, kit_id, veiculo_id)
                if dono != r["cliente"]:
                    cliente_errado += 1
                    continue
            conn.execute(
                "INSERT INTO remessa_kit (remessa_id, kit_id, veiculo_id, entrou_em, "
                " adicionado_por) VALUES (?, ?, ?, ?, ?)",
                (r["id"], kit_id, veiculo_id, now_brt(), user_id))
            entraram += 1
    fechou = False
    if entraram:
        atual = listar_uma(r["id"])
        if atual and atual["completa"]:
            _fechar_e_seguir(r, "alvo alcancado ao acrescentar a mao")
            fechou = True
    return {"entraram": entraram, "ja_em_outra": ja_em_outra,
            "cliente_errado": cliente_errado, "invalidos": invalidos,
            "fechou": fechou, "cliente": r["cliente"]}


def adicionar_kits(remessa_id: int, kit_ids: list) -> dict:
    """Compatibilidade: a versao antiga so falava de kit_id."""
    return adicionar_itens(remessa_id, ["kit:" + k for k in kit_ids if k])


def transferir(ref: str, destino_id: int, user_id: int = None) -> dict:
    """Move UM item de uma remessa pra outra.

    E o "o veiculo C devia estar na 002": em vez de tirar de uma e por na
    outra em dois passos (com o risco de esquecer o segundo), a troca e uma
    operacao so. Nao duplica: a linha ANTIGA e apagada, nunca copiada."""
    destino = listar_uma(int(destino_id))
    if not destino:
        raise ValueError("Remessa de destino nao encontrada.")
    if destino["status"] != "aberta":
        raise ValueError(f"A remessa {destino['nome']} esta {_situacao(destino)}. "
                         "So da pra receber item em remessa aberta.")
    kit_id, veiculo_id = _ler_ref(ref)
    with db() as conn:
        atual = conn.execute(
            "SELECT * FROM remessa_kit WHERE (kit_id IS NOT NULL AND kit_id = ?) "
            "   OR (veiculo_id IS NOT NULL AND veiculo_id = ?)",
            (kit_id, veiculo_id)).fetchone()
        if not atual:
            raise ValueError("Este item nao esta em remessa nenhuma — use Acrescentar.")
        if atual["remessa_id"] == destino["id"]:
            raise ValueError(f"Este item ja esta em {destino['nome']}.")
        origem_id = atual["remessa_id"]
        if destino["cliente"]:
            dono = _dono_do_item(conn, atual["kit_id"], atual["veiculo_id"])
            if dono != destino["cliente"]:
                raise ValueError(
                    f"{destino['nome']} e uma remessa de {destino['cliente']}, e este "
                    f"veiculo e de {dono or '(sem cliente)'}.")
        conn.execute("UPDATE remessa_kit SET remessa_id = ?, entrou_em = ?, "
                     "adicionado_por = ? WHERE id = ?",
                     (destino["id"], now_brt(), user_id, atual["id"]))
    novo = listar_uma(destino["id"])
    if novo and novo["completa"]:
        _fechar_e_seguir(novo, "alvo alcancado ao receber item de outra remessa")
    # As duas contagens sao lidas DEPOIS do movimento — antes, a origem
    # voltava com o numero velho e a tela dizia que nada tinha saido.
    return {"origem": listar_uma(origem_id), "destino": listar_uma(destino["id"])}


def transferir_varios(refs: list, destino_id: int, user_id: int = None) -> dict:
    """Move VARIOS itens de uma vez pra outra remessa.

    Item a item, o operador que seleciona dez e erra um levaria a operacao
    inteira pro chao (ou pior: metade movida, metade nao, sem saber qual).
    Aqui cada um e tentado por conta propria e o resultado diz quantos
    entraram e por que os outros ficaram — nomeando os veiculos, porque
    "3 falharam" nao ajuda ninguem a consertar."""
    destino = listar_uma(int(destino_id or 0))
    if not destino:
        raise ValueError("Escolha a remessa de destino.")
    if destino["status"] != "aberta":
        raise ValueError(f"A remessa {destino['nome']} esta {_situacao(destino)}. "
                         "So da pra receber item em remessa aberta.")
    movidos, recusados = 0, []
    origens = set()
    for ref in [r for r in refs if str(r).strip()]:
        try:
            with db() as conn:
                kit_id, veiculo_id = _ler_ref(ref)
                atual = conn.execute(
                    "SELECT remessa_id FROM remessa_kit WHERE "
                    "(kit_id IS NOT NULL AND kit_id = ?) OR "
                    "(veiculo_id IS NOT NULL AND veiculo_id = ?)",
                    (kit_id, veiculo_id)).fetchone()
            if atual:
                origens.add(atual["remessa_id"])
            transferir(ref, destino_id, user_id)
            movidos += 1
        except ValueError as e:
            recusados.append(_rotulo_do_ref(ref) + ": " + str(e))
    return {"movidos": movidos, "recusados": recusados,
            "destino": listar_uma(destino["id"]),
            "origens": [listar_uma(o) for o in origens if o]}


def remover_varios(remessa_id: int, refs: list) -> dict:
    """Tira varios itens da remessa de uma vez — o desfazer em lote."""
    r = listar_uma(remessa_id)
    if not r:
        raise ValueError("Remessa nao encontrada.")
    if r["status"] != "aberta":
        raise ValueError(f"A remessa {r['nome']} esta {_situacao(r)}. "
                         "Reabra antes de tirar itens dela.")
    tirados, recusados = 0, []
    for ref in [x for x in refs if str(x).strip()]:
        try:
            if remover_item(remessa_id, ref):
                tirados += 1
            else:
                recusados.append(_rotulo_do_ref(ref) + ": nao estava nesta remessa")
        except ValueError as e:
            recusados.append(_rotulo_do_ref(ref) + ": " + str(e))
    return {"tirados": tirados, "recusados": recusados}


def _rotulo_do_ref(ref: str) -> str:
    """O NUMERO do veiculo, que e como o operador chama o item — nao o kit_id,
    que ele nunca viu."""
    kit_id, veiculo_id = _ler_ref(ref)
    with db() as conn:
        if kit_id:
            row = conn.execute("SELECT veiculo FROM kit_record WHERE kit_id = ?",
                               (kit_id,)).fetchone()
            if row and row["veiculo"]:
                return str(row["veiculo"])
        if veiculo_id:
            row = conn.execute("SELECT numero FROM veiculos WHERE id = ?",
                               (veiculo_id,)).fetchone()
            if row:
                return str(row["numero"])
    return str(ref)


def remover_item(remessa_id: int, ref: str) -> bool:
    """Tira um item da remessa — o desfazer de quem acrescentou o errado.

    So de remessa ABERTA. Remessa fechada e historico: mudar a conta de um
    lote ja encerrado faria o numero passado pro cliente deixar de bater com
    o registro. Se precisar mesmo, reabra a remessa antes."""
    r = listar_uma(remessa_id)
    if not r:
        raise ValueError("Remessa nao encontrada.")
    if r["status"] != "aberta":
        raise ValueError(f"A remessa {r['nome']} esta {_situacao(r)}. "
                         "Reabra antes de tirar itens dela.")
    kit_id, veiculo_id = _ler_ref(ref)
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM remessa_kit WHERE remessa_id = ? AND "
            "((kit_id IS NOT NULL AND kit_id = ?) OR (veiculo_id IS NOT NULL AND veiculo_id = ?))",
            (remessa_id, kit_id, veiculo_id))
    return cur.rowcount > 0


def remover_kit(remessa_id: int, kit_id: str) -> bool:
    """Compatibilidade com a versao que so falava de kit_id."""
    return remover_item(remessa_id, "kit:" + str(kit_id))


def listar_uma(remessa_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM remessa WHERE id = ?", (remessa_id,)).fetchone()
    return _com_contagem(dict(row)) if row else None


def listar(limite: int = 60, incluir_arquivadas: bool = False) -> list:
    """Abertas primeiro, depois fechadas. Arquivadas ficam de fora por padrao
    — o ponto de arquivar e justamente tirar do caminho do dia a dia."""
    sql = ("SELECT r.*, u.nome AS criada_por_nome FROM remessa r "
           "LEFT JOIN users u ON u.id = r.criada_por ")
    if not incluir_arquivadas:
        sql += "WHERE r.status <> 'arquivada' "
    sql += ("ORDER BY CASE r.status WHEN 'aberta' THEN 0 WHEN 'fechada' THEN 1 ELSE 2 END, "
            "r.id DESC LIMIT ?")
    with db() as conn:
        rows = conn.execute(sql, (limite,)).fetchall()
    return [_com_contagem(dict(r)) for r in rows]


def kits_da_remessa(remessa_id: int) -> list:
    """Os itens daquela remessa — kits prontos E veiculos ainda sem kit.

    O LEFT JOIN no kit_record e o que deixa o veiculo "a produzir" aparecer:
    ele esta no lote, so nao virou kit ainda. Sem isso, a remessa mostraria
    45/90 e listaria 30 linhas, e ninguem entenderia a diferenca."""
    with db() as conn:
        rows = conn.execute(
            "SELECT rk.entrou_em, rk.kit_id, rk.veiculo_id, "
            "       COALESCE(kr.veiculo, v.numero) AS veiculo, "
            "       COALESCE(kr.garagem, v.garagem) AS garagem, "
            "       COALESCE(kr.modelo, v.modelo) AS modelo, "
            "       kr.nota_fiscal, kr.nota_fiscal_data, "
            "       COALESCE(kr.status_producao, "
            "                CASE WHEN ss.id IS NOT NULL THEN 'em_producao' "
            "                     ELSE 'a_produzir' END) AS status_producao, "
            "       kr.finalizado_em, kr.transito_em, "
            "       COALESCE(kt.nome, ktv.nome, v.modelo) AS kit_nome, "
            "       COALESCE(kt.cliente, v.cliente) AS cliente, "
            "       u.nome AS operador_nome, v.criado_em AS importado_em "
            "FROM remessa_kit rk "
            "LEFT JOIN kit_record kr ON kr.kit_id = rk.kit_id "
            "LEFT JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "LEFT JOIN veiculos v ON v.id = COALESCE(rk.veiculo_id, kr.veiculo_id) "
            "LEFT JOIN kit_template ktv ON LOWER(TRIM(ktv.nome)) = LOWER(TRIM(v.modelo)) "
            "                          AND ktv.ativo = 1 AND ktv.tipo = 'kit' "
            "LEFT JOIN scan_session ss ON ss.veiculo_id = rk.veiculo_id "
            "                         AND ss.status = 'em_andamento' "
            "LEFT JOIN users u ON u.id = kr.operador_id "
            "WHERE rk.remessa_id = ? "
            "GROUP BY rk.id "
            "ORDER BY rk.entrou_em, veiculo", (remessa_id,)).fetchall()
    saida = []
    for r in rows:
        d = dict(r)
        d["ref"] = ("kit:" + d["kit_id"]) if d["kit_id"] else ("veic:%d" % d["veiculo_id"])
        d["etapa"] = ("no_cliente" if (d["status_producao"] or "").startswith("cliente")
                      else d["status_producao"])
        d["etapa_texto"] = dict(ETAPAS).get(d["etapa"], d["etapa"] or "")
        saida.append(d)
    return saida
