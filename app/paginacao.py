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


def filtrar(lista: list, termo: str, campos) -> list:
    """Filtra a lista INTEIRA por um texto, antes de paginar.

    Existe porque a busca antes era só no navegador, escondendo linhas da
    página aberta: com a lista paginada, procurar algo que estivesse na
    página 7 não achava nada. Filtrando aqui, a busca varre tudo e a
    paginação passa a ser do resultado da busca.

    Compara sem acento nem maiúscula, e aceita várias palavras: todas
    precisam aparecer em algum dos campos (ordem não importa)."""
    termo = _normalizar(termo)
    if not termo:
        return lista
    palavras = termo.split()
    resultado = []
    for item in lista:
        alvo = " ".join(_normalizar(item.get(c)) for c in campos)
        if all(p in alvo for p in palavras):
            resultado.append(item)
    return resultado


def _normalizar(valor) -> str:
    """minúsculas e sem acento — 'Antena 5dBi' acha com 'antena', e
    'São Paulo' acha com 'sao paulo'."""
    import unicodedata
    texto = str(valor if valor is not None else "").strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def paginar(lista: list, pagina: int, por_pagina: int = POR_PAGINA_PADRAO) -> dict:
    """Fatia uma lista já carregada em memória pra exibição paginada.
    Use quando a lista completa já foi buscada do banco (sem LIMIT/OFFSET
    na consulta) — pra consultas grandes, prefira paginar direto no SQL."""
    total = len(lista)
    total_paginas = max(1, -(-total // por_pagina))  # ceil division
    pagina = max(1, min(pagina, total_paginas))
    inicio = (pagina - 1) * por_pagina
    itens = lista[inicio:inicio + por_pagina]
    return {
        "itens": itens,
        "pagina": pagina,
        "total_paginas": total_paginas,
        "total": total,
        # Faixa exibida, contada a partir de 1 pra ler na tela ("51–100 de
        # 112"). Numa lista vazia vira 0–0, e o macro de contagem troca por
        # "nenhum" em vez de mostrar faixa zerada.
        "inicio": inicio + 1 if itens else 0,
        "fim": inicio + len(itens),
        "exibindo": len(itens),
        "paginas_visiveis": janela_paginas(pagina, total_paginas),
    }
