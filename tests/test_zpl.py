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
    assert "abc-123" in zpl
    assert "Kit Teste" in zpl
    assert "Empresa X" in zpl
    assert "10/07/2026 14:30" in zpl
    assert "Joao" in zpl
    assert "Antena" in zpl
    assert "^XA" in zpl
    assert "^XZ" in zpl


def test_generate_zpl_estrutura_zebra():
    zpl = generate_zpl("id-1", "Kit", "Cliente", "Op", datetime.now(), [])
    assert "^BQN" in zpl   # QR code presente
    assert "^PW812" in zpl  # largura configurada
    assert "^LL1218" in zpl  # comprimento configurado
