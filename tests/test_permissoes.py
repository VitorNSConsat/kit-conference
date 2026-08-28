import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

os.environ["DB_PATH"] = ":memory:"

from database import init_db, db
import app.permissoes as permissoes_mod


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with db() as conn:
        conn.executescript("""
            DELETE FROM user_permissoes_negadas;
            DELETE FROM users;
        """)
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash, admin) VALUES "
            "(1, 'Admin', 'admin', 'x', 1)"
        )
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash, admin) VALUES "
            "(2, 'Comum', 'comum', 'x', 0)"
        )
    yield
    with db() as conn:
        conn.executescript("""
            DELETE FROM user_permissoes_negadas;
            DELETE FROM users;
        """)


def test_veiculos_excluir_existe_em_permissoes():
    assert "veiculos_excluir" in permissoes_mod.PERMISSOES
    assert "veiculos_excluir" in permissoes_mod.PERMISSOES_ACOES


def test_admin_sempre_pode_excluir_veiculo():
    admin = {"id": 1, "admin": 1}
    assert permissoes_mod.tem_permissao(admin, "veiculos_excluir") is True


def test_usuario_comum_pode_excluir_por_padrao():
    # Toda chave nova nasce PERMITIDA — ninguém perde acesso sem que um
    # admin negue explicitamente.
    comum = {"id": 2, "admin": 0}
    assert permissoes_mod.tem_permissao(comum, "veiculos_excluir") is True


def test_usuario_comum_sem_permissao_negada_explicitamente():
    comum = {"id": 2, "admin": 0}
    permissoes_mod.definir_permissoes(2, permitidas=set(permissoes_mod.PERMISSOES) - {"veiculos_excluir"})
    assert permissoes_mod.tem_permissao(comum, "veiculos_excluir") is False
    # E as outras permissões continuam liberadas — negar uma não nega todas.
    assert permissoes_mod.tem_permissao(comum, "ver_veiculos") is True


def test_grupos_cobre_todas_as_chaves_de_permissoes_acoes():
    # Cada chave de PERMISSOES_ACOES tem que estar em exatamente um dos
    # subgrupos de GRUPOS — senão o checkbox dela nunca aparece na tela de
    # usuários (essa foi a classe de bug que gerou este teste).
    chaves_em_grupos = set()
    for _titulo, chaves in permissoes_mod.GRUPOS:
        chaves_em_grupos |= set(chaves)
    assert chaves_em_grupos == set(permissoes_mod.PERMISSOES)
