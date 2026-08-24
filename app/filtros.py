"""Pedaços de SQL para os filtros de múltipla escolha das telas.

Cada filtro virou lista (dá pra ver duas garagens, dois operadores, três
ações), então o WHERE precisa de um IN com o número certo de interrogações.
Fica num lugar só pra nenhuma tela montar isso na mão — que é como nasce
concatenação de valor dentro de SQL.
"""


def em(coluna: str, valores) -> tuple[str, list]:
    """Devolve (" AND coluna IN (?, ?)", valores) — ou ("", []) quando não há
    filtro. Valor vazio é descartado: um checkbox sem valor não deve virar
    "IN ('')" e zerar a lista sem ninguém entender por quê."""
    vals = [str(v) for v in (valores or []) if str(v).strip() != ""]
    if not vals:
        return "", []
    marcas = ", ".join("?" for _ in vals)
    return f" AND {coluna} IN ({marcas})", vals


def lista(valores) -> list[str]:
    """Normaliza o que veio da query string: sem vazios, sem espaço sobrando."""
    return [str(v).strip() for v in (valores or []) if str(v).strip() != ""]
