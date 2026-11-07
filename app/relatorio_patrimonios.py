"""Relatório de patrimônios: todos os que o sistema tem, de onde vieram
(importação, bipagem, cadastro à mão...) e onde estão (em veículo, nunca
bipado, em bipagem...).

É a tela de VERIFICAR. Cadastrar, importar e corrigir continuam em Itens &
Estoque → Patrimônios; daqui o código leva pra página do patrimônio, onde
ficam as correções."""
from datetime import datetime

import app.items as items_mod
from database import db

ORIGENS = (
    ("importacao", "Importação"),
    ("bipagem", "Bipagem"),
    ("manual", "Cadastro manual"),
    ("bom", "Planilha BOM"),
    ("correcao", "Correção de código"),
)

SITUACOES = (
    ("ok", "Em veículo"),
    ("nunca_bipado", "Nunca bipado"),
    ("em_bipagem", "Em bipagem"),
    ("kit_sem_veiculo", "Kit sem veículo"),
    ("kit_removido", "Kit excluído"),
)


def _data(t):
    try:
        return datetime.strptime((t or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _origem(item: dict, primeira_bipagem: str | None) -> str:
    """Patrimônio novo já nasce com a origem gravada. Os de antes da coluna
    existir: se a primeira bipagem foi colada no cadastro (até 2 minutos),
    nasceu na bipagem; senão foi cadastrado à mão."""
    if item.get("origem"):
        return item["origem"]
    c, b = _data(item.get("criado_em")), _data(primeira_bipagem)
    if c and b and abs((b - c).total_seconds()) <= 120:
        return "bipagem"
    return "manual"


def listar() -> list[dict]:
    itens = items_mod.listar_itens()
    with db() as conn:
        primeira = {r[0]: r[1] for r in conn.execute(
            "SELECT si.codigo_barra, MIN(si.bipado_em) FROM scan_session_items si "
            "JOIN item_master im ON im.codigo_barra = si.codigo_barra GROUP BY si.codigo_barra")}
    nomes_origem, nomes_sit = dict(ORIGENS), dict(SITUACOES)
    for i in itens:
        i["origem_chave"] = _origem(i, primeira.get(i["codigo_barra"]))
        i["origem_texto"] = nomes_origem.get(i["origem_chave"], i["origem_chave"])
        i["situacao_curta"] = nomes_sit.get(i["situacao"], i["situacao"])
        i["primeira_bipagem"] = primeira.get(i["codigo_barra"])
    itens.sort(key=lambda i: (i["criado_em"] or ""), reverse=True)
    return itens


def filtrar(itens: list[dict], data_ini="", data_fim="", tipos=None, origens=None,
            situacoes=None, clientes=None, busca="") -> list[dict]:
    if data_ini:
        itens = [i for i in itens if (i["criado_em"] or "")[:10] >= data_ini]
    if data_fim:
        itens = [i for i in itens if (i["criado_em"] or "")[:10] <= data_fim]
    if tipos:
        alvo = {str(t) for t in tipos}
        itens = [i for i in itens if str(i["item_tipo_id"]) in alvo]
    if origens:
        itens = [i for i in itens if i["origem_chave"] in set(origens)]
    if situacoes:
        itens = [i for i in itens if i["situacao"] in set(situacoes)]
    if clientes:
        itens = [i for i in itens if (i["cliente_atual"] or "") in set(clientes)]
    termo = (busca or "").strip().lower()
    if termo:
        itens = [i for i in itens if termo in " ".join(str(x or "") for x in (
            i["codigo_barra"], i["descricao"], i["veiculo_atual"], i["cliente_atual"],
            i["serial_atual"], i.get("origem_ref"), i["criado_por_nome"])).lower()]
    return itens
