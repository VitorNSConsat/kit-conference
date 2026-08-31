"""Testes da auditoria de segurança: freio progressivo de login, mensagens
de erro que não vazam detalhe técnico, e freio de upload."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeURL:
    def __init__(self, path):
        self.path = path


class _FakeRequest:
    """Só o suficiente de Request pra exercitar _ip_do_cliente/_upload_liberado
    sem subir um servidor de verdade."""
    def __init__(self, ip="1.2.3.4", path="/admin/tipos/importar", headers=None):
        self.client = _FakeClient(ip)
        self.url = _FakeURL(path)
        self.headers = headers or {}


def _reset_freios(main):
    """Cada teste começa com os dicionários em memória zerados — eles são
    globais do módulo e sobreviveriam de um teste pro outro senão."""
    main._login_tentativas.clear()
    main._login_bloqueado_ate.clear()
    main._login_nivel_bloqueio.clear()
    main._upload_tentativas.clear()


@pytest.fixture
def main_mod():
    import main
    _reset_freios(main)
    yield main
    _reset_freios(main)


# ── Bloqueio progressivo de login ──────────────────────────────────────────

def test_login_libera_antes_do_limite(main_mod):
    chave = "1.2.3.4|fulano"
    for _ in range(main_mod._LOGIN_MAX_TENTATIVAS - 1):
        main_mod._login_falhou(chave)
    assert main_mod._login_bloqueado(chave) == 0


def test_login_bloqueia_no_limite(main_mod):
    chave = "1.2.3.4|fulano"
    for _ in range(main_mod._LOGIN_MAX_TENTATIVAS):
        main_mod._login_falhou(chave)
    espera = main_mod._login_bloqueado(chave)
    assert 0 < espera <= main_mod._LOGIN_JANELA_SEG


def test_login_segundo_bloqueio_e_mais_longo_que_o_primeiro(main_mod):
    chave = "1.2.3.4|fulano"
    # Primeiro ciclo de tentativas: bloqueia no nível 1.
    for _ in range(main_mod._LOGIN_MAX_TENTATIVAS):
        main_mod._login_falhou(chave)
    nivel1 = main_mod._login_bloqueado_ate[chave] - time.time()
    # Simula o bloqueio já ter passado, e um novo ciclo de tentativas erradas.
    main_mod._login_bloqueado_ate[chave] = 0
    for _ in range(main_mod._LOGIN_MAX_TENTATIVAS):
        main_mod._login_falhou(chave)
    nivel2 = main_mod._login_bloqueado_ate[chave] - time.time()
    assert nivel2 > nivel1 * 1.5   # dobrou (com folga pro tempo decorrido no teste)


def test_login_bloqueio_tem_teto(main_mod):
    chave = "1.2.3.4|fulano"
    # Força vários ciclos consecutivos de bloqueio.
    for nivel in range(1, 10):
        main_mod._login_bloqueado_ate[chave] = 0
        for _ in range(main_mod._LOGIN_MAX_TENTATIVAS):
            main_mod._login_falhou(chave)
    duracao = main_mod._login_bloqueado_ate[chave] - time.time()
    assert duracao <= main_mod._LOGIN_BLOQUEIO_TETO_SEG + 1


def test_login_ok_libera_tudo(main_mod):
    chave = "1.2.3.4|fulano"
    for _ in range(main_mod._LOGIN_MAX_TENTATIVAS):
        main_mod._login_falhou(chave)
    assert main_mod._login_bloqueado(chave) > 0
    main_mod._login_ok(chave)
    assert main_mod._login_bloqueado(chave) == 0
    assert chave not in main_mod._login_nivel_bloqueio


def test_login_chaves_diferentes_nao_se_afetam(main_mod):
    for _ in range(main_mod._LOGIN_MAX_TENTATIVAS):
        main_mod._login_falhou("1.2.3.4|fulano")
    assert main_mod._login_bloqueado("1.2.3.4|fulano") > 0
    assert main_mod._login_bloqueado("5.6.7.8|fulano") == 0
    assert main_mod._login_bloqueado("1.2.3.4|ciclano") == 0


# ── Mensagens de erro seguras ───────────────────────────────────────────────

def test_erro_usuario_deixa_passar_value_error(main_mod):
    e = ValueError("Este é o último administrador ativo.")
    assert main_mod._erro_usuario(e) == "Este é o último administrador ativo."


def test_erro_usuario_generico_para_outras_excecoes(main_mod, capsys):
    e = KeyError("detalhe_interno_que_nao_pode_vazar")
    msg = main_mod._erro_usuario(e)
    assert "detalhe_interno_que_nao_pode_vazar" not in msg
    assert msg == "Não foi possível concluir esta operação."
    # O detalhe tem que ir pro log do servidor, não desaparecer.
    saida = capsys.readouterr()
    assert "detalhe_interno_que_nao_pode_vazar" in saida.out


def test_erro_usuario_aceita_mensagem_generica_customizada(main_mod):
    e = RuntimeError("zipfile.BadZipFile: File is not a zip file")
    msg = main_mod._erro_usuario(e, "Não foi possível ler a planilha.")
    assert msg == "Não foi possível ler a planilha."
    assert "zipfile" not in msg


# ── Freio de upload ──────────────────────────────────────────────────────

def test_upload_liberado_ate_o_limite(main_mod):
    req = _FakeRequest()
    for _ in range(main_mod._UPLOAD_MAX_TENTATIVAS):
        assert main_mod._upload_liberado(req) is True


def test_upload_bloqueia_apos_o_limite(main_mod):
    req = _FakeRequest()
    for _ in range(main_mod._UPLOAD_MAX_TENTATIVAS):
        main_mod._upload_liberado(req)
    assert main_mod._upload_liberado(req) is False


def test_upload_rotas_diferentes_tem_freios_independentes(main_mod):
    req_a = _FakeRequest(path="/admin/tipos/importar")
    req_b = _FakeRequest(path="/admin/veiculos/import")
    for _ in range(main_mod._UPLOAD_MAX_TENTATIVAS):
        main_mod._upload_liberado(req_a)
    assert main_mod._upload_liberado(req_a) is False
    assert main_mod._upload_liberado(req_b) is True


# ── IP do cliente atrás do Cloudflare Tunnel ────────────────────────────────

def test_ip_do_cliente_ignora_header_por_padrao(main_mod, monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_IP", raising=False)
    req = _FakeRequest(ip="10.0.0.5", headers={"cf-connecting-ip": "9.9.9.9"})
    assert main_mod._ip_do_cliente(req) == "10.0.0.5"


def test_ip_do_cliente_confia_no_header_so_com_flag_ligada(main_mod, monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_IP", "1")
    req = _FakeRequest(ip="10.0.0.5", headers={"cf-connecting-ip": "9.9.9.9"})
    assert main_mod._ip_do_cliente(req) == "9.9.9.9"
