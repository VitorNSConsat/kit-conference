"""Testes de app/sessions.registrar_serial(): a extração/validação de MAC
address pra tipos marcados "Exige MAC address" (ex.: CVC) -- o serial deixou
de ser um número solto e virou o QR inteiro do equipamento, com o MAC
embutido em algum lugar. Tipos sem essa marcação continuam aceitando
qualquer serial, sem mudança de comportamento."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

os.environ["DB_PATH"] = ":memory:"

from database import init_db, db
from app import sessions as sessions_mod
import app.items as items_mod


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with db() as conn:
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash) VALUES (1, 'Teste', 'teste', 'x')"
        )
        # CVC: exige serial E o serial precisa ter um MAC reconhecível.
        conn.execute(
            "INSERT INTO item_tipo (id, nome, ativo, controle_externo, requer_serial, exigir_mac_address) "
            "VALUES (1, 'Vehicle computer CVC', 1, 1, 1, 1)"
        )
        # Roteador: também exige serial, mas SEM exigir MAC -- continua
        # aceitando qualquer texto, igual sempre foi.
        conn.execute(
            "INSERT INTO item_tipo (id, nome, ativo, controle_externo, requer_serial, exigir_mac_address) "
            "VALUES (2, 'Roteador', 1, 1, 1, 0)"
        )
        conn.execute(
            "INSERT INTO item_master (codigo_barra, item_tipo_id, ativo, criado_por) VALUES ('CVC001', 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO item_master (codigo_barra, item_tipo_id, ativo, criado_por) VALUES ('ROT001', 2, 1, 1)"
        )
        conn.execute(
            "INSERT INTO kit_template (id, nome, cliente, versao, ativo, criado_por) "
            "VALUES (1, 'Kit Teste', 'Cliente A', 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO kit_template_items (kit_template_id, item_tipo_id, quantidade_exigida, "
            "obrigatorio, requer_serial) VALUES (1, 1, 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO kit_template_items (kit_template_id, item_tipo_id, quantidade_exigida, "
            "obrigatorio, requer_serial) VALUES (1, 2, 1, 1, 1)"
        )
    yield
    with db() as conn:
        conn.executescript("""
            DELETE FROM kit_record;
            DELETE FROM scan_session_items;
            DELETE FROM scan_session;
            DELETE FROM kit_template_items;
            DELETE FROM kit_template;
            DELETE FROM item_master;
            DELETE FROM item_tipo;
            DELETE FROM users;
        """)


# ── _extrair_mac_address(): função pura ──────────────────────────────────────

def test_extrai_mac_com_separador_padrao():
    assert sessions_mod._extrair_mac_address("00:10:F3:E0:E8:3C") == "00:10:F3:E0:E8:3C"


def test_extrai_mac_com_caractere_do_leitor():
    # "Ç" no lugar de ":" -- é o que o leitor configurado pro CVC devolve.
    texto = "CON-001-0022_TSCFB2002846_B718_00Ç10ÇF3ÇE0ÇE8Ç3C;3D"
    assert sessions_mod._extrair_mac_address(texto) == "00:10:F3:E0:E8:3C"


def test_extrai_mac_normaliza_maiuscula():
    assert sessions_mod._extrair_mac_address("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"


def test_extrai_mac_ausente_retorna_none():
    assert sessions_mod._extrair_mac_address("003454") is None
    assert sessions_mod._extrair_mac_address("") is None
    assert sessions_mod._extrair_mac_address(None) is None


# ── registrar_serial(): fluxo completo de bipagem ────────────────────────────

def _bipar_ate_aguardando_serial(codigo_barra: str) -> int:
    sessao_id = sessions_mod.start_session(1, 1)
    resultado = sessions_mod.register_scan(sessao_id, codigo_barra)
    assert resultado["resultado"] == "aguardando_serial"
    return sessao_id


def test_cvc_recusa_serial_sem_mac():
    sessao_id = _bipar_ate_aguardando_serial("CVC001")
    resultado = sessions_mod.registrar_serial(sessao_id, "003454")
    assert resultado["resultado"] == "rejeitado"
    assert "MAC" in resultado["mensagem"]
    # Não travou a sessão: o item continua aguardando serial pra tentar de novo.
    assert sessions_mod.get_pendente_serial(sessao_id) is not None


def test_cvc_aceita_qrcode_com_mac_e_extrai():
    sessao_id = _bipar_ate_aguardando_serial("CVC001")
    qrcode = "CON-001-0022_TSCFB2002846_B718_00Ç10ÇF3ÇE0ÇE8Ç3C;3D"
    resultado = sessions_mod.registrar_serial(sessao_id, qrcode)
    assert resultado["resultado"] == "aceito"
    assert resultado["mac_address"] == "00:10:F3:E0:E8:3C"
    assert resultado["serial_number"] == qrcode
    with db() as conn:
        row = conn.execute(
            "SELECT serial_number, mac_address, status FROM scan_session_items "
            "WHERE sessao_id = ? AND codigo_barra = 'CVC001'", (sessao_id,)
        ).fetchone()
    assert row["serial_number"] == qrcode
    assert row["mac_address"] == "00:10:F3:E0:E8:3C"
    assert row["status"] == "completo"


def test_roteador_sem_exigir_mac_aceita_serial_qualquer():
    sessao_id = _bipar_ate_aguardando_serial("ROT001")
    resultado = sessions_mod.registrar_serial(sessao_id, "SN-12345")
    assert resultado["resultado"] == "aceito"
    assert resultado["mac_address"] is None


# ── o MAC extraído chega até o painel do veículo/patrimônio ──────────────────
# itens_do_kit() e onde_esta() alimentam _painel.html -- o pedido foi mostrar
# o MAC já formatado ali (em vez de só dentro do serial cru e ilegível), e
# isso só funciona se as duas consultas trouxerem a coluna.

def _finalizar_kit(sessao_id: int, kit_id: str = "kit-teste-001") -> str:
    with db() as conn:
        conn.execute(
            "INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, "
            "kit_template_versao, operador_id, veiculo) "
            "VALUES (?, ?, 1, 1, 1, 'VEIC-001')",
            (kit_id, sessao_id)
        )
    return kit_id


def test_itens_do_kit_traz_mac_do_item_com_mac():
    sessao_id = _bipar_ate_aguardando_serial("CVC001")
    sessions_mod.registrar_serial(sessao_id, "CON-001-0022_TSCFB2002846_B718_00Ç10ÇF3ÇE0ÇE8Ç3C;3D")
    sessions_mod.register_scan(sessao_id, "ROT001")
    sessions_mod.registrar_serial(sessao_id, "SN-12345")
    kit_id = _finalizar_kit(sessao_id)

    itens = sessions_mod.itens_do_kit(kit_id)
    cvc = next(i for i in itens if i["codigo_barra"] == "CVC001")
    roteador = next(i for i in itens if i["codigo_barra"] == "ROT001")
    assert cvc["mac_address"] == "00:10:F3:E0:E8:3C"
    assert roteador["mac_address"] is None


def test_onde_esta_traz_mac_do_item():
    sessao_id = _bipar_ate_aguardando_serial("CVC001")
    sessions_mod.registrar_serial(sessao_id, "CON-001-0022_TSCFB2002846_B718_00Ç10ÇF3ÇE0ÇE8Ç3C;3D")
    sessions_mod.register_scan(sessao_id, "ROT001")
    sessions_mod.registrar_serial(sessao_id, "SN-12345")
    _finalizar_kit(sessao_id)

    assert items_mod.onde_esta("CVC001")["mac_address"] == "00:10:F3:E0:E8:3C"
    assert items_mod.onde_esta("ROT001")["mac_address"] is None


# ── item bipado ANTES de mac_address existir como coluna: extrai na leitura ──
# registrar_serial() sempre grava mac_address hoje, mas bipagem antiga tem a
# coluna NULL com o MAC preso dentro do serial cru mesmo assim -- as funções
# que alimentam o painel precisam continuar mostrando o MAC pra essas também,
# não só pra quem bipar dagui pra frente.

def _bipar_serial_sem_extrair_mac(codigo_barra: str, serial: str) -> int:
    """Simula uma bipagem de antes da extração de MAC existir: grava o
    serial cru igual ao leitor manda, mas NUNCA preenche mac_address --
    fazendo à mão o que registrar_serial() fazia antes dessa função existir,
    já que hoje ele sempre extrai e não dá pra pedir pra ele "não extrair"."""
    sessao_id = _bipar_ate_aguardando_serial(codigo_barra)
    with db() as conn:
        pendente = conn.execute(
            "SELECT id FROM scan_session_items WHERE sessao_id = ? AND codigo_barra = ?",
            (sessao_id, codigo_barra)
        ).fetchone()
        conn.execute(
            "UPDATE scan_session_items SET serial_number = ?, mac_address = NULL, "
            "status = 'completo' WHERE id = ?",
            (serial, pendente["id"])
        )
    return sessao_id


def test_itens_do_kit_extrai_mac_de_bipagem_antiga_sem_coluna_preenchida():
    serial = "CON-001-0022_TSCFB2002846_B718_00Ç10ÇF3ÇE0ÇE8Ç3C;3D"
    sessao_id = _bipar_serial_sem_extrair_mac("CVC001", serial)
    sessions_mod.register_scan(sessao_id, "ROT001")
    sessions_mod.registrar_serial(sessao_id, "SN-12345")
    kit_id = _finalizar_kit(sessao_id, "kit-teste-002")

    with db() as conn:
        salvo = conn.execute(
            "SELECT mac_address FROM scan_session_items WHERE codigo_barra = 'CVC001'"
        ).fetchone()
    assert salvo["mac_address"] is None  # confirma que o cenário simulado é o de coluna vazia

    itens = sessions_mod.itens_do_kit(kit_id)
    cvc = next(i for i in itens if i["codigo_barra"] == "CVC001")
    assert cvc["mac_address"] == "00:10:F3:E0:E8:3C"


def test_onde_esta_extrai_mac_de_bipagem_antiga_sem_coluna_preenchida():
    serial = "CON-001-0022_TSCFB2002846_B718_00Ç10ÇF3ÇE0ÇE8Ç3C;3D"
    sessao_id = _bipar_serial_sem_extrair_mac("CVC001", serial)
    _finalizar_kit(sessao_id, "kit-teste-003")

    assert items_mod.onde_esta("CVC001")["mac_address"] == "00:10:F3:E0:E8:3C"


def test_historico_patrimonio_extrai_mac_de_bipagem_antiga():
    serial = "CON-001-0022_TSCFB2002846_B718_00Ç10ÇF3ÇE0ÇE8Ç3C;3D"
    sessao_id = _bipar_serial_sem_extrair_mac("CVC001", serial)
    _finalizar_kit(sessao_id, "kit-teste-004")

    historico = items_mod.historico_patrimonio("CVC001")
    assert historico[0]["mac_address"] == "00:10:F3:E0:E8:3C"
