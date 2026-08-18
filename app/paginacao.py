POR_PAGINA_PADRAO = 50


def janela_paginas(pagina: int, total_paginas: int, ao_redor: int = 2) -> list:
    """Números de página a mostrar no controle de paginação, com None
    marcando uma reticência ("..."). Sempre mostra a primeira, a última, e
    `ao_redor` páginas perto da atual — evita listar centenas de números
    quando o total de páginas é grande."""
    if total_paginas <= 1:
        return [1]
    paginas = {1, total_paginas}
    for p in range(pagina - ao_redor, pagina + ao_redor + 1):
        if 1 <= p <= total_paginas:
            paginas.add(p)
    ordenadas = sorted(paginas)
    resultado: list = []
    anterior = None
    for p in ordenadas:
        if anterior is not None and p - anterior > 1:
            resultado.append(None)
        resultado.append(p)
        anterior = p
    return resultado


def paginar(lista: list, pagina: int, por_pagina: int = POR_PAGINA_PADRAO) -> dict:
    """Fatia uma lista já carregada em memória pra exibição paginada.
    Use quando a lista completa já foi buscada do banco (sem LIMIT/OFFSET
    na consulta) — pra consultas grandes, prefira paginar direto no SQL."""
    total = len(lista)
    total_paginas = max(1, -(-total // por_pagina))  # ceil division
    pagina = max(1, min(pagina, total_paginas))
    inicio = (pagina - 1) * por_pagina
    return {
        "itens": lista[inicio:inicio + por_pagina],
        "pagina": pagina,
        "total_paginas": total_paginas,
        "total": total,
        "paginas_visiveis": janela_paginas(pagina, total_paginas),
    }
