"""Esteira de produção: acompanha o ciclo de vida de um Kit da bipagem até
a instalação confirmada no cliente.

    Em Produção (Consat)  →  Produzido  →  Em Trânsito  →  Cliente: Em
    Produção (instalando) →  Cliente: Produzido (concluído)

"Em Produção (Consat)" não mora em kit_record — vem de scan_session ainda
em andamento, então não existe kit_record até a bipagem ser finalizada.
Do ponto de finalização em diante, tudo mora em kit_record.status_producao.

Só Kits entram nessa esteira — Pedidos continuam com o fluxo próprio de
"Feito/Não feito" que já existia.
"""

import re
from datetime import datetime, timedelta

from database import db, now_brt
import app.auditoria as auditoria_mod
import app.sessions as sessions_mod
import app.kit_templates as templates_mod

ESTAGIOS = ["produzido", "transito", "cliente_instalando", "cliente_concluido"]

# Limites de exibição do painel da TV. São só filtro de tela — nada sai do
# banco nem dos relatórios, e aumentar o limite depois faz os antigos
# voltarem a aparecer. 0 = sem limite.
TV_CONFIG_PADRAO = {
    "tv_limite_em_producao": 12,
    "tv_limite_produzido": 12,
    "tv_limite_transito": 12,
    "tv_limite_cliente_instalando": 12,
    "tv_limite_cliente_concluido": 12,
    # Por quantas horas um kit concluído no cliente continua aparecendo na
    # TV depois de concluído. 0 = fica pra sempre (só o limite corta).
    "tv_horas_cliente_concluido": 24,
}


def get_tv_config() -> dict:
    """Config de exibição da TV, com os padrões preenchidos pra qualquer
    chave que ainda não tenha sido salva."""
    cfg = dict(TV_CONFIG_PADRAO)
    with db() as conn:
        for r in conn.execute("SELECT chave, valor FROM producao_config").fetchall():
            if r["chave"] in cfg:
                try:
                    cfg[r["chave"]] = int(r["valor"])
                except (TypeError, ValueError):
                    pass
    return cfg


def salvar_tv_config(valores: dict) -> None:
    """Grava só as chaves conhecidas, sempre como inteiro >= 0 — entrada
    inválida cai pro padrão em vez de quebrar a tela da TV."""
    with db() as conn:
        for chave, padrao in TV_CONFIG_PADRAO.items():
            if chave not in valores:
                continue
            try:
                v = max(0, int(valores[chave]))
            except (TypeError, ValueError):
                v = padrao
            conn.execute(
                "INSERT INTO producao_config (chave, valor) VALUES (?, ?) "
                "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                (chave, str(v))
            )


def _aplicar_limite(lista: list, limite: int) -> list:
    """Janela rolante: mantém só os `limite` mais recentes. As listas já
    vêm ordenadas do mais antigo pro mais novo (ou o contrário, no caso do
    concluído), então cortar pelo fim/começo certo faz o item novo empurrar
    o mais antigo pra fora. limite 0 = sem corte."""
    if limite and len(lista) > limite:
        return lista[-limite:]
    return lista


def atribuir_sequencia(sessao_id: int) -> int:
    """Número da etiqueta "Em Andamento" — atribuído na 1ª impressão dessa
    sessão e fixo depois disso (reimprimir não muda o número). Continua de
    onde o contador global parou; zerar_sequencia() reinicia do zero."""
    with db() as conn:
        atual = conn.execute(
            "SELECT sequencia FROM scan_session WHERE id = ?", (sessao_id,)
        ).fetchone()
        if atual and atual["sequencia"] is not None:
            return atual["sequencia"]
        conn.execute("UPDATE producao_sequencia SET valor = valor + 1 WHERE id = 1")
        novo = conn.execute("SELECT valor FROM producao_sequencia WHERE id = 1").fetchone()["valor"]
        conn.execute("UPDATE scan_session SET sequencia = ? WHERE id = ?", (novo, sessao_id))
        return novo


def zerar_sequencia() -> None:
    with db() as conn:
        conn.execute("UPDATE producao_sequencia SET valor = 0 WHERE id = 1")


def _percentual_concluido(kit_template_id: int, sessao_id: int) -> int:
    """% de itens obrigatórios já bipados nessa sessão — a mesma conta que
    libera o botão "Finalizar" na tela de bipagem (itens opcionais não
    contam, pra não distorcer o progresso de quem ainda não bipou nada
    opcional). Cada item é limitado ao que falta dele, então bipar demais
    de um item não estoura o percentual pra além de 100%."""
    itens = [i for i in templates_mod.get_itens_template(kit_template_id) if i["obrigatorio"]]
    if not itens:
        return 100
    contagem = sessions_mod.get_contagem(sessao_id)
    exigido = sum(i["quantidade_exigida"] for i in itens)
    bipado = sum(min(contagem.get(i["item_tipo_id"], 0), i["quantidade_exigida"]) for i in itens)
    if exigido == 0:
        return 100
    return round(bipado / exigido * 100)


# ── Kits a produzir ───────────────────────────────────────────────────────────
# Etapa ANTES de "Em Produção": veículo pronto pra bipar que ainda não teve
# bipagem nenhuma. A regra não é inventada aqui — é exatamente a mesma que
# decide se o veículo aparece na tela de destino ao iniciar uma bipagem:
#
#   ativo + cliente + garagem preenchida        (definir_destino exige garagem)
#   + modelo que casa com um kit ATIVO           (veiculos.listar(modelo=...))
#   + não ocupado                                (veiculos.esta_ocupado)
#
# Se um veículo está aqui, ele aparece pra ser bipado; se não está, não
# aparece. Uma regra só, então a lista nunca promete um kit que a bipagem
# não deixa começar.
#
# O template vem de subconsulta e não de JOIN pelo nome: dois kits ativos
# com o mesmo nome fariam o JOIN duplicar o veículo, e aí a contagem e a
# lista discordariam — foi assim que o relatório de kits escondeu registro.
_A_PRODUZIR_FROM = """
    FROM veiculos v
    JOIN kit_template kt ON kt.id = (
        SELECT k2.id FROM kit_template k2
         WHERE LOWER(TRIM(k2.nome)) = LOWER(TRIM(v.modelo))
           AND k2.ativo = 1 AND k2.tipo = 'kit'
         ORDER BY k2.versao DESC, k2.id DESC LIMIT 1)
    WHERE v.ativo = 1
      AND TRIM(COALESCE(v.cliente, '')) != ''
      AND TRIM(COALESCE(v.garagem, '')) != ''
      AND TRIM(COALESCE(v.modelo,  '')) != ''
      -- Bipagem em andamento = já é "Em Produção", nunca "a produzir".
      AND NOT EXISTS (SELECT 1 FROM scan_session ss
                       WHERE ss.veiculo_id = v.id AND ss.status = 'em_andamento')
      -- Já tem kit pronto = saiu desta etapa. A exceção é o veículo
      -- liberado à mão (liberado_em), que volta a valer pra uma bipagem
      -- nova — mesma exceção que esta_ocupado() aplica.
      AND (v.liberado_em IS NOT NULL
           OR NOT EXISTS (SELECT 1 FROM kit_record kr WHERE kr.veiculo_id = v.id))
"""


def listar_a_produzir() -> list[dict]:
    """Veículos prontos pra produção que ainda não começaram."""
    with db() as conn:
        rows = conn.execute(f"""
            SELECT v.id AS veiculo_id, v.numero AS veiculo, v.cliente,
                   v.garagem, v.modelo, v.liberado_em,
                   kt.id AS kit_template_id, kt.nome AS kit_nome, kt.versao
            {_A_PRODUZIR_FROM}
            ORDER BY v.cliente, v.numero
        """).fetchall()
    return [dict(r) for r in rows]


def contar_a_produzir() -> int:
    """Mesmo FROM/WHERE da listagem — a contagem não tem como divergir do
    tamanho da lista."""
    with db() as conn:
        return conn.execute(f"SELECT COUNT(*) {_A_PRODUZIR_FROM}").fetchone()[0]


def localizacao_dos_veiculos() -> dict[int, dict]:
    """{veiculo_id: {texto, cor}} — onde cada veículo está AGORA no fluxo.

    Não é campo novo: é derivado dos mesmos estados que o painel de
    Produção já usa, na ordem em que o veículo os percorre. Por isso a
    localização se atualiza sozinha conforme ele avança, sem ninguém
    marcar nada.

    Uma consulta só pra todos os veículos — a tela lista centenas, e uma
    consulta por linha (N+1) seria a diferença entre abrir na hora e
    travar. O estágio mais AVANÇADO vence: um veículo com kit no cliente e
    outro kit em produção aparece como "em produção", que é o que está
    acontecendo com ele agora."""
    ordem = {  # maior = mais avançado
        "a_produzir": 1, "em_producao": 5, "produzido": 2,
        "transito": 3, "cliente_instalando": 4, "cliente_concluido": 4,
    }
    rotulos = {
        "a_produzir":         ("Galpão — A produzir", "#2980b9"),
        "em_producao":        ("Galpão — Em produção", "#e67e22"),
        "produzido":          ("Galpão — Produzido", "#27ae60"),
        "transito":           ("Em trânsito", "#8e44ad"),
        "cliente_instalando": ("Cliente", "#16a085"),
        "cliente_concluido":  ("Cliente", "#16a085"),
    }
    estado: dict[int, str] = {}
    cliente_de: dict[int, str] = {}

    def _melhor(vid, novo):
        atual = estado.get(vid)
        if atual is None or ordem[novo] > ordem[atual]:
            estado[vid] = novo

    with db() as conn:
        # Kits já feitos, do estágio de cada um.
        for r in conn.execute(
            "SELECT kr.veiculo_id, kr.status_producao, kt.cliente "
            "FROM kit_record kr JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "WHERE kr.veiculo_id IS NOT NULL"
        ).fetchall():
            est = r["status_producao"] if r["status_producao"] in ordem else "produzido"
            _melhor(r["veiculo_id"], est)
            if est.startswith("cliente"):
                cliente_de[r["veiculo_id"]] = r["cliente"] or ""
        # Bipagem em andamento vence tudo: é onde o veículo está agora.
        for r in conn.execute(
            "SELECT veiculo_id FROM scan_session "
            "WHERE status = 'em_andamento' AND veiculo_id IS NOT NULL"
        ).fetchall():
            _melhor(r["veiculo_id"], "em_producao")
        # Prontos pra produzir (mesma regra da etapa do painel).
        for r in conn.execute(f"SELECT v.id AS vid {_A_PRODUZIR_FROM}").fetchall():
            _melhor(r["vid"], "a_produzir")

    saida = {}
    for vid, est in estado.items():
        texto, cor = rotulos[est]
        if est.startswith("cliente") and cliente_de.get(vid):
            texto = f"Cliente — {cliente_de[vid]}"
        saida[vid] = {"texto": texto, "cor": cor, "estado": est}
    return saida


def listar_em_producao() -> list[dict]:
    """Sessões de bipagem de Kit ainda em andamento — a etapa "Em Produção"
    do lado Consat, que não tem kit_record ainda."""
    with db() as conn:
        rows = conn.execute(
            "SELECT s.id AS sessao_id, s.iniciado_em, s.kit_template_id, "
            "s.veiculo, s.garagem, "
            # Modelo do CADASTRO do veículo, com o da sessão como reserva:
            # s.modelo é uma cópia tirada no momento do destino, então
            # corrigir o modelo no cadastro não se refletiria aqui. Só cai
            # pro valor da sessão quando o veículo não está cadastrado.
            "COALESCE(NULLIF(TRIM(v.modelo), ''), s.modelo) AS modelo, "
            "t.nome AS kit_nome, t.cliente, "
            "u.nome AS operador_nome "
            "FROM scan_session s "
            "JOIN kit_template t ON t.id = s.kit_template_id "
            "JOIN users u ON u.id = s.operador_id "
            "LEFT JOIN veiculos v ON v.id = s.veiculo_id "
            "WHERE s.status = 'em_andamento' AND t.tipo = 'kit' "
            "ORDER BY s.iniciado_em"
        ).fetchall()
    sessoes = [dict(r) for r in rows]
    for s in sessoes:
        s["percentual"] = _percentual_concluido(s["kit_template_id"], s["sessao_id"])
    return sessoes


_CAMPOS_KIT = (
    "kr.kit_id, kr.finalizado_em, kr.transito_em, kr.cliente_instalando_em, "
    "kr.cliente_concluido_em, kr.veiculo, kr.garagem, kr.modelo, "
    "kr.nota_fiscal, kr.nota_fiscal_data, "
    "kt.nome AS kit_nome, kt.cliente, u.nome AS operador_nome"
)


def _listar_por_estagio(estagio: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            f"SELECT {_CAMPOS_KIT} "
            "FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "JOIN users u ON u.id = kr.operador_id "
            "WHERE kr.status_producao = ? AND kt.tipo = 'kit' "
            "ORDER BY kr.finalizado_em",
            (estagio,)
        ).fetchall()
    return [dict(r) for r in rows]


def listar_produzido() -> list[dict]:
    """Produzidos que ainda não foram marcados como em trânsito — fila
    acumulada, não reseta sozinha: o que não for movido continua aparecendo
    nos dias seguintes até alguém decidir."""
    return _listar_por_estagio("produzido")


def listar_transito() -> list[dict]:
    return _listar_por_estagio("transito")


def listar_cliente_instalando() -> list[dict]:
    return _listar_por_estagio("cliente_instalando")


def listar_cliente_concluido(limite: int | None = None) -> list[dict]:
    with db() as conn:
        query = (
            f"SELECT {_CAMPOS_KIT} "
            "FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "JOIN users u ON u.id = kr.operador_id "
            "WHERE kr.status_producao = 'cliente_concluido' AND kt.tipo = 'kit' "
            "ORDER BY kr.cliente_concluido_em DESC"
        )
        if limite:
            query += f" LIMIT {int(limite)}"
        rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def listar_no_cliente(limite: int | None = None) -> list[dict]:
    """Tudo que JÁ ESTÁ no cliente, numa lista só — instalando e concluído
    juntos, do mais recente pro mais antigo.

    A separação some da TELA, não do banco: status_producao continua com os
    dois valores, cada kit mantém suas datas e o botão de concluir/voltar
    segue funcionando. Fundir os estágios no banco apagaria o histórico de
    quando cada kit chegou e quando foi concluído — o que a unificação pede
    é uma visão única de "está no cliente", e é isso que esta função dá."""
    query = (
        f"SELECT {_CAMPOS_KIT}, kr.status_producao, "
        "COALESCE(kr.cliente_concluido_em, kr.cliente_instalando_em) AS chegou_em "
        "FROM kit_record kr "
        "JOIN kit_template kt ON kt.id = kr.kit_template_id "
        "JOIN users u ON u.id = kr.operador_id "
        "WHERE kr.status_producao IN ('cliente_instalando', 'cliente_concluido') "
        "AND kt.tipo = 'kit' "
        "ORDER BY chegou_em DESC"
    )
    if limite:
        query += f" LIMIT {int(limite)}"
    with db() as conn:
        rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def dados_tv() -> dict:
    """Listas do painel da TV já com os limites de exibição aplicados.
    Puramente visual: o que fica de fora continua no banco, nos relatórios
    e na tela de controle /admin/producao — e volta a aparecer se o limite
    for aumentado."""
    cfg = get_tv_config()
    horas = cfg["tv_horas_cliente_concluido"]

    concluido = listar_cliente_concluido()
    if horas:
        limite_tempo = (datetime.now() - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
        concluido = [k for k in concluido
                     if (k.get("cliente_concluido_em") or "") >= limite_tempo]
    # listar_cliente_concluido já vem do mais recente pro mais antigo
    if cfg["tv_limite_cliente_concluido"]:
        concluido = concluido[:cfg["tv_limite_cliente_concluido"]]

    return {
        "em_producao": _aplicar_limite(listar_em_producao(), cfg["tv_limite_em_producao"]),
        "produzido": _aplicar_limite(listar_produzido(), cfg["tv_limite_produzido"]),
        "transito": _aplicar_limite(listar_transito(), cfg["tv_limite_transito"]),
        "cliente_instalando": _aplicar_limite(
            listar_cliente_instalando(), cfg["tv_limite_cliente_instalando"]),
        "cliente_concluido": concluido,
    }


def atualizar_nota_fiscal(kit_id: str, nota_fiscal: str, nota_fiscal_data: str,
                           motivo: str = "") -> bool:
    """Registro manual só pro controle interno — não faz parte da esteira,
    não trava nem depende de estágio.

    Se já havia nota fiscal salva e o valor está mudando, exige motivo —
    quem só está preenchendo pela primeira vez não precisa justificar nada.
    Sem motivo nesse caso, não grava e retorna False; o motivo em si não é
    guardado em kit_record, ele fica só no log de auditoria (que já
    registra os campos exatos submetidos no formulário)."""
    nota_fiscal = nota_fiscal.strip()
    nota_fiscal_data = nota_fiscal_data.strip()
    motivo = motivo.strip()
    with db() as conn:
        atual = conn.execute(
            "SELECT nota_fiscal, nota_fiscal_data FROM kit_record WHERE kit_id = ?",
            (kit_id,)
        ).fetchone()
        tinha_valor = bool(atual and (atual["nota_fiscal"] or "").strip())
        mudou = tinha_valor and (
            (atual["nota_fiscal"] or "").strip() != nota_fiscal
            or (atual["nota_fiscal_data"] or "").strip() != nota_fiscal_data
        )
        if mudou and not motivo:
            return False
        conn.execute(
            "UPDATE kit_record SET nota_fiscal = ?, nota_fiscal_data = ? WHERE kit_id = ?",
            (nota_fiscal, nota_fiscal_data or None, kit_id)
        )
        return True


def atribuir_nota_em_lote(kit_ids: list[str], nota_fiscal: str,
                          nota_fiscal_data: str, motivo: str = "") -> dict:
    """Mesma nota e mesma data pra vários kits de uma vez.

    Reaproveita atualizar_nota_fiscal() kit a kit em vez de escrever um
    UPDATE ... IN (...): assim a regra do motivo obrigatório pra SOBRESCREVER
    nota já existente vale igual no lote e no individual — um UPDATE em massa
    passaria por cima dela em silêncio.

    Age SÓ nos kit_ids recebidos. Devolve o que foi gravado e o que ficou
    bloqueado por falta de motivo, pra tela poder dizer exatamente o que
    aconteceu em vez de só um número."""
    atualizados, bloqueados = [], []
    for kit_id in kit_ids:
        kit_id = (kit_id or "").strip()
        if not kit_id:
            continue
        if atualizar_nota_fiscal(kit_id, nota_fiscal, nota_fiscal_data, motivo):
            atualizados.append(kit_id)
        else:
            bloqueados.append(kit_id)
    return {"atualizados": atualizados, "bloqueados": bloqueados}


_RE_KIT_ID_PATH = re.compile(
    r"^/admin/producao/([^/]+)/(cliente-instalando|cliente-concluido|voltar|nota-fiscal)$"
)
_RE_KIT_IDS_DETALHE = re.compile(r"kit_ids=([^|]+)")


def _kit_ids_da_linha(caminho: str, detalhe: str) -> list[str]:
    """Extrai o(s) kit_id envolvido(s) num registro de auditoria da
    esteira — a maioria das rotas leva o kit_id na própria URL, só o envio
    em lote pra 'em trânsito' leva vários dentro do corpo do formulário."""
    m = _RE_KIT_ID_PATH.match(caminho)
    if m:
        return [m.group(1)]
    if caminho == "/admin/producao/transito":
        return [v.strip() for v in _RE_KIT_IDS_DETALHE.findall(detalhe or "")]
    return []


def _parse_detalhe(detalhe: str) -> dict:
    """O log de auditoria grava o formulário cru como 'chave=valor | chave=valor'
    — aqui a gente quebra de volta num dict pra montar frases legíveis."""
    campos = {}
    for parte in (detalhe or "").split(" | "):
        if "=" in parte:
            chave, _, valor = parte.partition("=")
            campos[chave] = valor
    return campos


def _descricao_amigavel(linha: dict) -> str:
    """Traduz a ação + o formulário cru numa frase pronta pro histórico —
    em vez de 'kit_ids=... | nota_fiscal=212 | nota_fiscal_data=...'."""
    acao = linha["acao"]
    campos = _parse_detalhe(linha["detalhe"] or "")
    if acao == "PRODUCAO: NOTA FISCAL":
        nf = campos.get("nota_fiscal") or "—"
        data = campos.get("nota_fiscal_data")
        texto = f"Nota fiscal alterada para {nf}"
        if data:
            texto += f" (data {data})"
        motivo = campos.get("motivo")
        if motivo:
            texto += f" — motivo: {motivo}"
        return texto
    if acao == "PRODUCAO: EM TRANSITO":
        n = len(_kit_ids_da_linha(linha["caminho"], linha["detalhe"] or ""))
        return f"Marcou {n} kit(s) como Em Trânsito"
    if acao == "PRODUCAO: CHEGADA NO CLIENTE":
        return "Kit marcado como chegando no cliente"
    if acao == "PRODUCAO: INSTALACAO CONCLUIDA":
        return "Instalação marcada como concluída"
    if acao == "PRODUCAO: VOLTAR ESTAGIO":
        return "Kit voltou para o estágio anterior"
    if linha["caminho"].endswith("/zerar-sequencia"):
        return "Contador de sequência (etiqueta Em Andamento) zerado"
    return linha["detalhe"] or "—"


# Teto do histórico da esteira, um só pra tela e pra exportação — com
# valores diferentes, o mesmo período mostrava quantidades diferentes
# em cada lugar. É alto de propósito: quem limita o volume é o
# período escolhido; isto aqui é só uma trava contra carregar anos
# inteiros de auditoria de uma vez.
LIMITE_HISTORICO = 5000


def listar_historico(data_ini: str = "", data_fim: str = "",
                     limite: int = LIMITE_HISTORICO) -> list[dict]:
    """Histórico de ações manuais da esteira (mudança de estágio + edição
    de nota fiscal), lido da auditoria geral (que já cobre toda rota de
    /admin/producao automaticamente) e enriquecido com veículo/garagem
    quando dá pra casar o kit_id da linha."""
    linhas = auditoria_mod.listar(
        data_ini=data_ini, data_fim=data_fim,
        caminho_prefixo="/admin/producao/", limite=limite
    )
    with db() as conn:
        kits = {
            r["kit_id"]: dict(r)
            for r in conn.execute(
                "SELECT kr.kit_id, kr.veiculo, kr.garagem, kt.nome AS kit_nome "
                "FROM kit_record kr JOIN kit_template kt ON kt.id = kr.kit_template_id"
            ).fetchall()
        }
    for linha in linhas:
        descricoes = []
        for kid in _kit_ids_da_linha(linha["caminho"], linha["detalhe"] or ""):
            info = kits.get(kid)
            if info and (info["veiculo"] or "").strip():
                descricoes.append(info["veiculo"])
            elif info:
                descricoes.append(info["kit_nome"])
            else:
                descricoes.append(kid)
        linha["kit_desc"] = ", ".join(descricoes) if descricoes else "—"
        linha["resumo"] = _descricao_amigavel(linha)
    return linhas


def _buscar_estagio(conn, kit_id: str) -> str | None:
    row = conn.execute(
        "SELECT status_producao FROM kit_record WHERE kit_id = ?", (kit_id,)
    ).fetchone()
    return row["status_producao"] if row else None


def marcar_transito(kit_ids: list[str]) -> int:
    """Move em lote de 'produzido' para 'transito'. Ignora silenciosamente
    kit_id que não esteja em 'produzido' (evita corrida: alguém marcou duas
    vezes, ou o kit já foi movido por outra aba)."""
    if not kit_ids:
        return 0
    agora = now_brt()
    with db() as conn:
        placeholders = ",".join("?" * len(kit_ids))
        cur = conn.execute(
            f"UPDATE kit_record SET status_producao = 'transito', transito_em = ? "
            f"WHERE kit_id IN ({placeholders}) AND status_producao = 'produzido'",
            [agora, *kit_ids]
        )
        return cur.rowcount


def marcar_cliente_instalando(kit_id: str) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE kit_record SET status_producao = 'cliente_instalando', "
            "cliente_instalando_em = ? WHERE kit_id = ? AND status_producao = 'transito'",
            (now_brt(), kit_id)
        )
        return cur.rowcount > 0


def marcar_cliente_concluido(kit_id: str) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE kit_record SET status_producao = 'cliente_concluido', "
            "cliente_concluido_em = ? WHERE kit_id = ? AND status_producao = 'cliente_instalando'",
            (now_brt(), kit_id)
        )
        return cur.rowcount > 0


def voltar_estagio(kit_id: str) -> bool:
    """Desfaz um clique errado, voltando um estágio e limpando o timestamp
    daquele estágio (ele deixa de valer)."""
    with db() as conn:
        atual = _buscar_estagio(conn, kit_id)
        if atual is None or atual == "produzido":
            return False  # já está no início da esteira, nada a desfazer
        idx = ESTAGIOS.index(atual)
        anterior = ESTAGIOS[idx - 1]
        campo_ts = {
            "transito": "transito_em",
            "cliente_instalando": "cliente_instalando_em",
            "cliente_concluido": "cliente_concluido_em",
        }[atual]
        conn.execute(
            f"UPDATE kit_record SET status_producao = ?, {campo_ts} = NULL WHERE kit_id = ?",
            (anterior, kit_id)
        )
        return True


def resumo() -> dict:
    """Contagens pra faixa de resumo do painel — uma consulta só."""
    with db() as conn:
        em_producao = conn.execute(
            "SELECT COUNT(*) FROM scan_session s JOIN kit_template t ON t.id = s.kit_template_id "
            "WHERE s.status = 'em_andamento' AND t.tipo = 'kit'"
        ).fetchone()[0]
        linhas = conn.execute(
            "SELECT kr.status_producao, COUNT(*) AS n FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "WHERE kt.tipo = 'kit' GROUP BY kr.status_producao"
        ).fetchall()
    contagem = {r["status_producao"]: r["n"] for r in linhas}
    return {
        # Mesma consulta que alimenta a lista (contar_a_produzir usa o
        # _A_PRODUZIR_FROM), então o número do cabeçalho é sempre o número
        # de linhas da tabela.
        "a_produzir": contar_a_produzir(),
        "em_producao": em_producao,
        "produzido": contagem.get("produzido", 0),
        "transito": contagem.get("transito", 0),
        "cliente_instalando": contagem.get("cliente_instalando", 0),
        "cliente_concluido": contagem.get("cliente_concluido", 0),
        # Card unificado de Cliente — os dois estágios somados. Os dois
        # números separados continuam aí porque o Painel da TV os usa.
        "cliente": contagem.get("cliente_instalando", 0) + contagem.get("cliente_concluido", 0),
    }
