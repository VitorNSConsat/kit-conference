"""Importar patrimônios por planilha.

As planilhas de controle chegam como uma GRADE de números (uma coluna por
lote, sem cabeçalho) com um título solto em cima — ex.: "Controle de
patrimônio Microfones". Por isso a leitura não depende de coluna: varre todas
as células e fica com o que parece código (sem espaço e com algum dígito). O
título e os cabeçalhos ficam de fora sozinhos.

Importado, o patrimônio fica no catálogo (item_master) com o tipo escolhido e
a bipagem passa a reconhecê-lo direto — sem perguntar "que item é este?".

Duas etapas, como a importação de veículos: `previa()` mostra o que vai
acontecer (novos, já cadastrados, repetidos na planilha, conflito com código
de estoque) e só `importar()` grava — recalculando tudo, então a prévia
nunca é confiada às cegas."""
import io
import re
import unicodedata
from collections import Counter
from datetime import date, datetime

from database import db, now_brt

# Teto de segurança: uma planilha de controle tem centenas de códigos; dezenas
# de milhares é arquivo errado.
LIMITE_CODIGOS = 20000
DIGITOS_PADRAO = 6     # os patrimônios da casa são 6 dígitos (001144, 007552)


def _texto(v) -> str:
    if v is None or isinstance(v, bool) or isinstance(v, (datetime, date)):
        return ""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else ""
    if isinstance(v, int):
        return str(v)
    return str(v).strip()


def _eh_codigo(t: str) -> bool:
    # "0", "000" não são patrimônio (coluna de contagem, célula zerada).
    return (bool(t) and len(t) <= 60 and not re.search(r"\s", t)
            and any(c.isdigit() for c in t) and t.strip("0") != "")


def normalizar(codigo: str, completar_zeros: bool) -> str:
    codigo = (codigo or "").strip()
    if completar_zeros and codigo.isdigit() and len(codigo) < DIGITOS_PADRAO:
        return codigo.zfill(DIGITOS_PADRAO)
    return codigo


def _ordem(c: str):
    return (0, int(c), c) if c.isdigit() else (1, 0, c)


def ler_planilha(conteudo: bytes) -> dict:
    """{titulo, codigos (na ordem da planilha, coluna a coluna), ignoradas}.
    Lê todas as abas."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    except Exception:
        raise ValueError("Não foi possível abrir o arquivo. Envie uma planilha .xlsx.")
    titulo, codigos, ignoradas = "", [], 0
    for ws in wb.worksheets:
        linhas = [list(r) for r in ws.iter_rows(values_only=True)]
        largura = max((len(r) for r in linhas), default=0)
        for col in range(largura):
            for r in linhas:
                t = _texto(r[col] if col < len(r) else None)
                if not t:
                    continue
                if _eh_codigo(t):
                    codigos.append(t)
                else:
                    ignoradas += 1
                    if not titulo:
                        titulo = t
        if len(codigos) > LIMITE_CODIGOS:
            break
    wb.close()
    if not codigos:
        raise ValueError("Nenhum código de patrimônio encontrado na planilha.")
    if len(codigos) > LIMITE_CODIGOS:
        raise ValueError(f"A planilha tem mais de {LIMITE_CODIGOS} códigos — confira se é o arquivo certo.")
    return {"titulo": titulo, "codigos": codigos, "ignoradas": ignoradas}


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()


_PALAVRAS_VAZIAS = {"controle", "patrimonio", "patrimonios", "para", "lista", "planilha", "itens", "item"}


def sugerir_tipo(titulo: str, tipos: list[dict]) -> int | None:
    """Tipo cujo nome mais combina com o título da planilha (ex.: "Controle
    de patrimônio Microfones" → "Microfone ..."). Compara pelo começo das
    palavras (singular/plural casam; prefixo curto não). None quando nada
    combina."""
    palavras = [p for p in re.findall(r"[a-z0-9]+", _sem_acento(titulo))
                if len(p) >= 4 and p not in _PALAVRAS_VAZIAS]
    if not palavras:
        return None

    def casa(a: str, b: str) -> bool:
        # Mesmo começo E tamanho parecido: "microfones" casa com "microfone",
        # mas não com "micro" (de "micro HDMI").
        return len(b) >= 4 and a[:5] == b[:5] and abs(len(a) - len(b)) <= 2

    melhor, melhor_pontos = None, 0
    for t in tipos:
        nome = re.findall(r"[a-z0-9]+", _sem_acento(t["nome"]))
        pontos = sum(1 for p in palavras if any(casa(p, n) for n in nome))
        if pontos > melhor_pontos:
            melhor, melhor_pontos = t["id"], pontos
    return melhor


def previa(codigos_brutos: list[str], completar_zeros: bool) -> dict:
    """O que a importação faria, sem gravar nada.

      novos        — entram no catálogo;
      cadastrados  — já existem (no mesmo formato ou com/sem zeros à
                     esquerda): ficam como estão, com o tipo que já têm;
      estoque      — o código é de uma caixa de estoque (código fixo), não
                     pode virar patrimônio;
      repetidos    — aparecem mais de uma vez na planilha (entram uma vez)."""
    codigos = [normalizar(c, completar_zeros) for c in codigos_brutos if (c or "").strip()]
    cont = Counter(codigos)
    unicos = list(dict.fromkeys(codigos))
    # Formas equivalentes (com e sem zeros) pra não criar 5430 ao lado de 005430.
    formas = {}
    for c in unicos:
        formas[c] = c
        if c.isdigit():
            formas.setdefault(c.lstrip("0") or "0", c)
            formas.setdefault(c.zfill(DIGITOS_PADRAO), c)
    chaves = list(formas)
    cadastrados, estoque = {}, set()
    with db() as conn:
        for i in range(0, len(chaves), 500):
            lote = chaves[i:i + 500]
            marc = ",".join("?" * len(lote))
            for r in conn.execute(
                    f"SELECT im.codigo_barra, t.nome FROM item_master im "
                    f"JOIN item_tipo t ON t.id = im.item_tipo_id "
                    f"WHERE im.codigo_barra IN ({marc})", lote).fetchall():
                orig = formas[r["codigo_barra"]]
                cadastrados.setdefault(orig, {"codigo": orig, "no_sistema": r["codigo_barra"], "tipo": r["nome"]})
            for r in conn.execute(f"SELECT codigo_barra FROM estoque WHERE codigo_barra IN ({marc})", lote).fetchall():
                estoque.add(formas[r["codigo_barra"]])
    novos = sorted((c for c in unicos if c not in cadastrados and c not in estoque), key=_ordem)
    return {
        "total_lidos": len(codigos),
        "novos": novos,
        "cadastrados": sorted(cadastrados.values(), key=lambda d: _ordem(d["codigo"])),
        "estoque": sorted(estoque - set(cadastrados), key=_ordem),
        "repetidos": sorted((c for c, n in cont.items() if n > 1), key=_ordem),
        "completar_zeros": completar_zeros,
    }


def importar(codigos_brutos: list[str], completar_zeros: bool, item_tipo_id: int,
             user_id: int | None, arquivo: str = "") -> dict:
    """Grava os novos (recalcula a prévia na hora). Devolve a prévia com o
    número de criados."""
    with db() as conn:
        tipo = conn.execute("SELECT id, nome FROM item_tipo WHERE id = ?", (item_tipo_id,)).fetchone()
    if not tipo:
        raise ValueError("Escolha o tipo de item dos patrimônios.")
    p = previa(codigos_brutos, completar_zeros)
    ref = (arquivo or "planilha").strip()[:120]
    agora = now_brt()
    criados = 0
    with db() as conn:
        for c in p["novos"]:
            cur = conn.execute(
                "INSERT INTO item_master (codigo_barra, item_tipo_id, criado_por, criado_em, origem, origem_ref) "
                "SELECT ?, ?, ?, ?, 'importacao', ? "
                "WHERE NOT EXISTS (SELECT 1 FROM item_master WHERE codigo_barra = ?)",
                (c, item_tipo_id, user_id, agora, ref, c))
            criados += cur.rowcount
    p["criados"] = criados
    p["tipo_nome"] = tipo["nome"]
    return p
