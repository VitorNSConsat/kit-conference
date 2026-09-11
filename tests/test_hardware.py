"""Testes de app/hardware.py: reabertura exige motivo, resolver grava os
três campos atomicamente, upload recusa extensão/tamanho fora do permitido,
resumo() bate com listar(), e a última ação de cada frente (Brasil/Suécia/
Fabricante) é calculada corretamente a partir da timeline de eventos."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.hardware as hw


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    # O banco :memory: fica vivo durante toda a sessão — limpa as tabelas de
    # hardware antes de cada teste pra um teste não ver as ocorrências que
    # o teste anterior criou (users fica intocado, cada teste cria o seu).
    with db() as conn:
        for tabela in ("hardware_anexo", "hardware_ocorrencia_evento", "hardware_ocorrencia", "hardware_produto"):
            conn.execute(f"DELETE FROM {tabela}")
    yield


@pytest.fixture
def usuario_id():
    # O banco :memory: fica vivo durante toda a sessão de testes (não é
    # recriado por teste) — username precisa ser único por teste, senão
    # colide com o usuário que um teste anterior já inseriu.
    username = f"teste-{uuid.uuid4().hex[:8]}"
    with db() as conn:
        conn.execute(
            "INSERT INTO users (username, nome, password_hash, admin, ativo) "
            "VALUES (?, 'Usuário Teste', 'x', 1, 1)", (username,))
        return conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]


def _criar_ocorrencia(usuario_id, **extra):
    dados = {"cliente": "Eletra", "produto_nome": "MX4", "categoria_defeito": "Não liga"}
    dados.update(extra)
    return hw.criar(dados, usuario_id)


def test_criar_exige_cliente(usuario_id):
    with pytest.raises(ValueError):
        hw.criar({"cliente": ""}, usuario_id)


def test_reabrir_recusa_sem_motivo(usuario_id):
    oid = _criar_ocorrencia(usuario_id)
    hw.resolver(oid, "Reparado", "Consertado.", None, usuario_id)
    with pytest.raises(ValueError):
        hw.reabrir(oid, "", usuario_id)
    with pytest.raises(ValueError):
        hw.reabrir(oid, "   ", usuario_id)


def test_reabrir_recusa_ocorrencia_nao_encerrada(usuario_id):
    oid = _criar_ocorrencia(usuario_id)
    with pytest.raises(ValueError):
        hw.reabrir(oid, "Motivo qualquer", usuario_id)


def test_reabrir_funciona_com_motivo(usuario_id):
    oid = _criar_ocorrencia(usuario_id)
    hw.resolver(oid, "Reparado", "Consertado.", None, usuario_id)
    hw.reabrir(oid, "Cliente reportou o mesmo defeito", usuario_id)
    o = hw.buscar(oid)
    assert o["status"] == "em_andamento"
    tipos = [e["tipo"] for e in hw.listar_eventos(oid)]
    assert "reabertura" in tipos


def test_resolver_grava_os_tres_campos_atomicamente(usuario_id):
    oid = _criar_ocorrencia(usuario_id)
    hw.resolver(oid, "Substituído", "Placa principal trocada.", "2026-01-15", usuario_id)
    o = hw.buscar(oid)
    assert o["status"] == "resolvido"
    assert o["resultado_final"] == "Substituído"
    assert o["solucao_texto"] == "Placa principal trocada."
    assert o["data_solucao"] == "2026-01-15"


def test_resolver_recusa_resultado_invalido(usuario_id):
    oid = _criar_ocorrencia(usuario_id)
    with pytest.raises(ValueError):
        hw.resolver(oid, "Resultado inventado", "texto", None, usuario_id)


def test_resumo_bate_com_listar(usuario_id):
    _criar_ocorrencia(usuario_id, prioridade="critica")
    _criar_ocorrencia(usuario_id)
    oid3 = _criar_ocorrencia(usuario_id)
    hw.resolver(oid3, "Reparado", "ok", None, usuario_id)

    resumo = hw.resumo()
    lista = hw.listar()
    assert resumo["total"] == len(lista) == 3
    assert resumo["nao_iniciadas"] == 2
    assert resumo["concluidas"] == 1
    assert resumo["criticas"] == 1


def test_resumo_por_cliente_agrupa_e_calcula_percentual(usuario_id):
    _criar_ocorrencia(usuario_id, cliente="Eletra")
    _criar_ocorrencia(usuario_id, cliente="Eletra")
    oid3 = _criar_ocorrencia(usuario_id, cliente="Eletra")
    hw.resolver(oid3, "Reparado", "ok", None, usuario_id)
    _criar_ocorrencia(usuario_id, cliente="REDEMOB")

    resumo = hw.resumo_por_cliente()
    por_nome = {r["cliente"]: r for r in resumo}

    assert por_nome["Eletra"]["total"] == 3
    assert por_nome["Eletra"]["nao_iniciadas"] == 2
    assert por_nome["Eletra"]["concluidas"] == 1
    assert por_nome["Eletra"]["percentual_concluido"] == 33
    # 2 de 3 ainda "não iniciado" (aberto) -> badge do card é "não iniciado";
    # a resolvida não conta pro predominante, já que só olha ocorrências abertas.
    assert por_nome["Eletra"]["status_predominante"] == "nao_iniciado"
    assert por_nome["REDEMOB"]["total"] == 1

    # nome_asc é o padrão: ordem alfabética
    nomes = [r["cliente"] for r in hw.resumo_por_cliente(ordenar="nome_asc")]
    assert nomes == sorted(nomes, key=str.lower)

    # qtd_desc: quem tem mais equipamento vem primeiro
    nomes_qtd = [r["cliente"] for r in hw.resumo_por_cliente(ordenar="qtd_desc")]
    assert nomes_qtd[0] == "Eletra"


def test_resumo_por_cliente_status_predominante_cai_pra_resolvido_quando_tudo_concluido(usuario_id):
    oid1 = _criar_ocorrencia(usuario_id, cliente="REDEMOB")
    oid2 = _criar_ocorrencia(usuario_id, cliente="REDEMOB")
    hw.resolver(oid1, "Reparado", "ok", None, usuario_id)
    hw.resolver(oid2, "Substituído", "ok", None, usuario_id)

    resumo = {r["cliente"]: r for r in hw.resumo_por_cliente()}
    assert resumo["REDEMOB"]["status_predominante"] == "resolvido"
    assert resumo["REDEMOB"]["status_predominante_texto"] == "Resolvido"


def test_resumo_por_cliente_ignora_arquivadas(usuario_id):
    oid = _criar_ocorrencia(usuario_id, cliente="Eletra")
    hw.arquivar(oid)
    resumo = hw.resumo_por_cliente()
    assert all(r["cliente"] != "Eletra" for r in resumo)


def test_arquivar_some_da_listagem_mas_nao_do_banco(usuario_id):
    oid = _criar_ocorrencia(usuario_id)
    hw.arquivar(oid)
    assert all(o["id"] != oid for o in hw.listar())
    with db() as conn:
        row = conn.execute("SELECT ativo FROM hardware_ocorrencia WHERE id = ?", (oid,)).fetchone()
    assert row["ativo"] == 0


def test_ultimas_acoes_pega_a_mais_recente_de_cada_area(usuario_id):
    oid = _criar_ocorrencia(usuario_id)
    hw.registrar_acao(oid, "brasil", "Primeira ação Brasil.", "Em andamento", usuario_id)
    hw.registrar_acao(oid, "brasil", "Segunda ação Brasil (mais recente).", "Concluída", usuario_id)
    hw.registrar_acao(oid, "suecia", "Ação Suécia.", "Aguardando retorno", usuario_id)

    o = hw.buscar(oid)
    assert o["acoes"]["brasil"]["conteudo"] == "Segunda ação Brasil (mais recente)."
    assert o["acoes"]["brasil"]["situacao"] == "Concluída"
    assert o["acoes"]["suecia"]["conteudo"] == "Ação Suécia."
    assert "fabricante" not in o["acoes"]

    historico_brasil = hw.listar_eventos_area(oid, "brasil")
    assert len(historico_brasil) == 2


def test_registrar_acao_recusa_area_invalida(usuario_id):
    oid = _criar_ocorrencia(usuario_id)
    with pytest.raises(ValueError):
        hw.registrar_acao(oid, "marte", "conteudo", "situacao", usuario_id)


def test_salvar_anexo_recusa_extensao_fora_da_lista(tmp_path, usuario_id, monkeypatch):
    monkeypatch.chdir(tmp_path)
    oid = _criar_ocorrencia(usuario_id)
    with pytest.raises(ValueError):
        hw.salvar_anexo(oid, "malicioso.exe", b"conteudo", "application/octet-stream", usuario_id)


def test_salvar_anexo_recusa_acima_do_teto(tmp_path, usuario_id, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hw, "MAX_ANEXO_BYTES", 10)
    oid = _criar_ocorrencia(usuario_id)
    with pytest.raises(ValueError):
        hw.salvar_anexo(oid, "foto.png", b"x" * 100, "image/png", usuario_id)


def test_salvar_anexo_aceita_e_lista(tmp_path, usuario_id, monkeypatch):
    monkeypatch.chdir(tmp_path)
    oid = _criar_ocorrencia(usuario_id)
    anexo_id = hw.salvar_anexo(oid, "relatorio.pdf", b"%PDF-1.4 conteudo", "application/pdf", usuario_id)
    anexos = hw.listar_anexos(oid)
    assert len(anexos) == 1
    assert anexos[0]["id"] == anexo_id
    assert anexos[0]["nome_arquivo"] == "relatorio.pdf"
    assert os.path.exists(anexos[0]["caminho_disco"])
    tipos = [e["tipo"] for e in hw.listar_eventos(oid)]
    assert "anexo" in tipos


def test_editar_basico_registra_evento_so_quando_prioridade_muda(usuario_id):
    oid = _criar_ocorrencia(usuario_id, prioridade="baixa")
    hw.editar_basico(oid, {"cliente": "Eletra", "prioridade": "baixa"}, usuario_id)
    eventos = hw.listar_eventos(oid)
    assert not any(e["tipo"] == "prioridade" for e in eventos)

    hw.editar_basico(oid, {"cliente": "Eletra", "prioridade": "critica"}, usuario_id)
    eventos = hw.listar_eventos(oid)
    assert any(e["tipo"] == "prioridade" for e in eventos)
    assert hw.buscar(oid)["prioridade"] == "critica"


def test_criar_produto_recusa_nome_duplicado(usuario_id):
    hw.criar_produto("MX4", "Consat", "Computador de bordo")
    with pytest.raises(ValueError):
        hw.criar_produto("mx4", "Outro", "")


# ── Catálogos editáveis (status/prioridade/categoria/localização) ──────────

def test_catalogo_criar_editar_desativar_reativar():
    nome = f"Local teste {uuid.uuid4().hex[:8]}"
    oid = hw.criar_opcao("localizacao", nome)
    opcao = next(o for o in hw.listar_opcoes("localizacao") if o["id"] == oid)
    assert opcao["nome"] == nome
    assert opcao["ativo"] == 1
    assert opcao["sistema"] == 0

    hw.editar_opcao("localizacao", oid, nome + " (editado)")
    assert hw.opcoes_mapa("localizacao")[opcao["chave"]]["nome"] == nome + " (editado)"

    hw.desativar_opcao("localizacao", oid)
    assert all(o["id"] != oid for o in hw.listar_opcoes("localizacao"))
    assert any(o["id"] == oid for o in hw.listar_opcoes("localizacao", incluir_inativas=True))

    hw.reativar_opcao("localizacao", oid)
    assert any(o["id"] == oid for o in hw.listar_opcoes("localizacao"))


def test_catalogo_recusa_desativar_item_de_sistema():
    resolvido_id = hw.opcoes_mapa("status")["resolvido"]["id"]
    with pytest.raises(ValueError):
        hw.desativar_opcao("status", resolvido_id)

    normal_id = hw.opcoes_mapa("prioridade")["normal"]["id"]
    with pytest.raises(ValueError):
        hw.desativar_opcao("prioridade", normal_id)


def test_catalogo_criar_gera_chave_unica_quando_nomes_colidem():
    base = f"Aguardando teste {uuid.uuid4().hex[:8]}"
    id1 = hw.criar_opcao("status", base)
    id2 = hw.criar_opcao("status", base + "!!")  # slugifica pro mesmo texto base
    chave1 = next(o["chave"] for o in hw.listar_opcoes("status") if o["id"] == id1)
    chave2 = next(o["chave"] for o in hw.listar_opcoes("status") if o["id"] == id2)
    assert chave1 != chave2


def test_editar_opcao_categoria_propaga_para_ocorrencias_existentes(usuario_id):
    nome = f"Categoria teste {uuid.uuid4().hex[:8]}"
    cat_id = hw.criar_opcao("categoria", nome)
    oid = _criar_ocorrencia(usuario_id, categoria_defeito=nome)

    hw.editar_opcao("categoria", cat_id, nome + " renomeada")
    assert hw.buscar(oid)["categoria_defeito"] == nome + " renomeada"


def test_desativar_opcao_localizacao_mantem_texto_em_ocorrencia_ja_gravada(usuario_id):
    nome = f"Prateleira teste {uuid.uuid4().hex[:8]}"
    loc_id = hw.criar_opcao("localizacao", nome)
    chave = next(o["chave"] for o in hw.listar_opcoes("localizacao") if o["id"] == loc_id)
    oid = _criar_ocorrencia(usuario_id, localizacao=chave)

    hw.desativar_opcao("localizacao", loc_id)
    assert chave not in hw.opcoes_mapa("localizacao", incluir_inativas=False)
    assert hw.buscar(oid)["localizacao_texto"] == nome


def test_status_terminais_reflete_catalogo_dinamico():
    assert "resolvido" in hw.status_terminais()
    novo_id = hw.criar_opcao("status", f"Status terminal teste {uuid.uuid4().hex[:8]}", eh_terminal=True)
    chave = next(o["chave"] for o in hw.listar_opcoes("status") if o["id"] == novo_id)
    assert chave in hw.status_terminais()
