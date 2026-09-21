"""Filtros da lista de RMAs: a URL montada por hardware_qs() preserva o estado
(filtros, ordenação, escopo), descarta o que é transitório e os chips de filtro
ativo removem só o próprio filtro."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from urllib.parse import parse_qsl, urlsplit

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

import main


def _pares(url):
    return parse_qsl(urlsplit(url).query)


def test_qs_preserva_filtros_e_ordenacao_ao_trocar_de_pagina():
    atual = [("cliente", "Eletra"), ("status", "aberto"), ("ord_hw", "id"), ("dir_hw", "asc")]
    url = main.hardware_qs(atual, pag_hw=3)
    assert url.startswith("/admin/hardware?")
    assert set(_pares(url)) == set(atual) | {("pag_hw", "3")}


def test_qs_descarta_mensagens_transitorias_e_troca_valor():
    atual = [("prioridade", "alta"), ("ok", "arquivadas_lote"), ("qtd", "2"), ("pag_hw", "4")]
    url = main.hardware_qs(atual, pag_hw=None, prioridade="normal")
    assert _pares(url) == [("prioridade", "normal")]


def test_qs_vazio_devolve_so_o_caminho():
    assert main.hardware_qs([], busca=None) == "/admin/hardware"


def test_qs_repete_chave_para_lista():
    url = main.hardware_qs([], status=["a", "b"])
    assert _pares(url) == [("status", "a"), ("status", "b")]


def test_chips_removem_so_o_proprio_filtro_e_voltam_a_pagina_1():
    params = [("busca", "mx4"), ("status", "a"), ("status", "b"), ("responsavel", "Ana"),
              ("pag_hw", "3"), ("ord_hw", "id")]
    filtros = {"busca": "mx4", "status": ["a", "b"], "responsavel": "Ana"}
    chips = main._hardware_chips(params, filtros, {"status": {"a": "Aberto", "b": "Fechado"}})
    assert [c["rotulo"] for c in chips] == ["Busca: mx4", "Status: Aberto", "Status: Fechado", "Responsável: Ana"]
    sem_a = dict(_pares(chips[1]["url"]))
    assert sem_a.get("status") == "b" and sem_a.get("busca") == "mx4" and sem_a.get("ord_hw") == "id"
    assert "pag_hw" not in sem_a
    sem_busca = dict(_pares(chips[0]["url"]))
    assert "busca" not in sem_busca and sem_busca.get("responsavel") == "Ana"
