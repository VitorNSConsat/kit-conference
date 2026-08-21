"""Filtro de período, num lugar só.

Antes cada relatório montava o próprio `AND DATE(coluna) >= ?` à mão. Seis
implementações separadas da mesma regra é onde nasce a divergência entre a
tela e a exportação, então tudo que filtra por período passa por aqui.

Por que INTERVALO SEMIABERTO (`>= início` e `< início do dia seguinte`) e
não `DATE(coluna) BETWEEN`:

1. `DATE(coluna)` envolve a coluna numa função, e aí o SQLite não consegue
   usar o índice — a consulta vira varredura da tabela inteira. Comparando a
   coluna crua, o índice (idx_kr_finalizado e afins) entra em ação.
2. O semiaberto pega o dia inteiro sem depender de "23:59:59": registro
   gravado às 23:59:59.4 (se um dia passar a ter fração de segundo) ainda
   entra, porque o corte é o instante 00:00:00 do dia seguinte.

A comparação é textual, e funciona porque todo timestamp do sistema é
gravado por now_brt() no formato 'YYYY-MM-DD HH:MM:SS' — ordem alfabética
igual à ordem cronológica, e já em horário de Brasília (não UTC), que é o
mesmo fuso que o usuário digita no filtro. Não há conversão de fuso em
lugar nenhum do caminho, e é justamente por isso que não há como um filtro
de 21/08 acabar consultando 20/08 21:00.
"""
from datetime import datetime, timedelta

_FORMATO_DIA = "%Y-%m-%d"


def _dia(texto: str) -> datetime | None:
    """Lê 'YYYY-MM-DD' (o que o <input type=date> manda). Qualquer outra
    coisa vira None — filtro inválido é filtro ausente, nunca um período
    torto que esconde registros em silêncio."""
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return datetime.strptime(texto[:10], _FORMATO_DIA)
    except ValueError:
        return None


def intervalo(data_ini: str = "", data_fim: str = "") -> tuple[str | None, str | None]:
    """Devolve (início, fim_exclusivo) prontos pra comparar com a coluna.

    intervalo('2026-08-21', '2026-08-21')
        -> ('2026-08-21 00:00:00', '2026-08-22 00:00:00')

    O fim é o começo do dia SEGUINTE ao data_fim — é o que faz um filtro de
    um dia só pegar o dia inteiro, incluindo 23:59:59.

    Datas invertidas (fim antes do início) são devolvidas como estão: quem
    chama decide, e devolver um intervalo vazio silenciosamente esconderia
    registros sem explicar por quê."""
    ini = _dia(data_ini)
    fim = _dia(data_fim)
    return (
        ini.strftime("%Y-%m-%d 00:00:00") if ini else None,
        (fim + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00") if fim else None,
    )


def clausula(coluna: str, data_ini: str = "", data_fim: str = "") -> tuple[str, list]:
    """Pedaço de SQL + parâmetros pro filtro de período de `coluna`.

        sql, p = clausula("kr.finalizado_em", data_ini, data_fim)
        query += sql
        params += p

    `coluna` é interpolada direto na string, então SÓ pode receber nome de
    coluna vindo do código — nunca algo digitado pelo usuário. As datas vão
    como parâmetro ligado, normalizadas antes: texto que não é uma data é
    descartado em _dia() e nem chega ao banco."""
    ini, fim = intervalo(data_ini, data_fim)
    sql, params = "", []
    if ini:
        sql += f" AND {coluna} >= ?"
        params.append(ini)
    if fim:
        sql += f" AND {coluna} < ?"
        params.append(fim)
    return sql, params
