import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from app.zpl import generate_zpl


def test_generate_zpl_contém_kit_id():
    zpl = generate_zpl(
        kit_id="abc-123",
        kit_nome="Kit Teste",
        cliente="Empresa X",
        operador="Joao",
        timestamp=datetime(2026, 7, 10, 14, 30),
        itens=[{"descricao": "Antena", "quantidade": 2}]
    )
    # ID do kit (upper) presente no QR e no rodapé
    assert "ABC-123" in zpl
    # Data e hora presentes
    assert "10/07/2026" in zpl
    assert "14:30" in zpl
    # URL do kit presente no QR
    assert "/kit/" in zpl
    assert "^XA" in zpl
    assert "^XZ" in zpl


def test_generate_zpl_estrutura_zebra():
    zpl = generate_zpl("id-1", "Kit", "Cliente", "Op", datetime.now(), [])
    assert "^BQN" in zpl   # QR code presente
    assert "^PW800" in zpl  # largura 100mm a 203 DPI
    assert "^LL1200" in zpl  # altura 150mm a 203 DPI


def test_generate_zpl_veiculo_garagem():
    zpl = generate_zpl(
        kit_id="xyz-999",
        kit_nome="Kit A",
        cliente="C",
        operador="Op",
        timestamp=datetime(2026, 7, 10, 9, 0),
        itens=[],
        veiculo="ABC-1234",
        garagem="G03",
    )
    assert "ABC-1234" in zpl
    assert "G03" in zpl
