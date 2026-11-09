"""Cópia de segurança do banco — a que roda sozinha, de tempo em tempo.

O sistema já copiava o banco antes de mudar a estrutura
(database._backup_antes_de_migrar), mas isso é raro por natureza: só acontece
quando sai uma versão que mexe em tabela. Entre uma migração e outra o banco
podia passar meses sem uma cópia — e é justamente nesse meio que estão as
bipagens do dia, os kits, o estoque e a auditoria.

Aqui a cópia é por RELÓGIO: todo dia no horário configurado (23:00 de padrão),
enquanto o sistema estiver no ar. Se o servidor estava desligado na hora, a
cópia sai assim que ele voltar. Não existe agendador do Windows no meio.

Além da diária, toda GRANDE MUDANÇA (importação, sobressalente com muitas
unidades, remessa ou lote grande) gera uma cópia antes e outra depois. Essas
ficam fora do rodízio: nunca são apagadas sozinhas.

Duas decisões que valem a explicação:

1. A cópia usa o backup ONLINE do próprio SQLite (`Connection.backup`), não
   `shutil.copy`. Copiar o arquivo com o sistema rodando pode pegar o banco no
   meio de uma escrita e gerar uma cópia corrompida — que só se descobre no
   dia em que ela precisa ser usada. O backup online conversa com o SQLite e
   sai sempre consistente, mesmo com gente bipando.

2. A cópia vai pra DUAS pastas quando há uma segunda configurada: a de sempre,
   ao lado do banco, e a que o operador escolher (OneDrive, Drive, HD externo,
   pasta de rede). Backup no mesmo disco do banco só protege contra engano
   humano; contra disco queimado, não protege nada.
"""

import os
import shutil
import sqlite3
from datetime import datetime, timedelta

from database import db, now_brt, _get_db_path

# Chaves gravadas em backup_config. Tudo texto no banco, como nas outras
# configurações do sistema; get_config() devolve já convertido.
CONFIG_PADRAO = {
    # Liga/desliga só a rotina automática — o backup manual continua.
    "ativo": "1",
    # Hora da cópia diária (hora do Brasil).
    "horario": "23:00",
    # A partir de quanto uma mudança é "grande" e ganha cópia antes e depois.
    "limite_importacao": "50",      # linhas da planilha
    "limite_sobressalente": "100",  # unidades no pacote
    "limite_lote": "20",            # veículos/kits de uma vez
    # Quantas cópias ficam guardadas. As mais antigas somem sozinhas.
    "manter": "30",
    # Segunda pasta (opcional). Vazio = só a pasta padrão.
    "pasta_extra": "",
}

MANTER_MIN, MANTER_MAX = 1, 999
LIMITE_MIN, LIMITE_MAX = 1, 1000000

# Motivos que o rodízio não apaga.
MOTIVOS_PROTEGIDOS = ("migracao", "grande_antes", "grande_depois")
_UNIDADES = {"importacao": "linhas", "sobressalente": "unidades", "lote": "itens"}

MOTIVOS = {
    "automatico": "Automático",
    "manual": "Manual",
    "migracao": "Antes de migração",
    "grande_antes": "Antes de grande mudança",
    "grande_depois": "Depois de grande mudança",
}


# ── Configuração ─────────────────────────────────────────────────────────
def get_config() -> dict:
    cfg = dict(CONFIG_PADRAO)
    with db() as conn:
        for r in conn.execute("SELECT chave, valor FROM backup_config").fetchall():
            if r["chave"] in cfg:
                cfg[r["chave"]] = r["valor"]
    cfg["ativo"] = 1 if str(cfg["ativo"]) == "1" else 0
    cfg["horario"] = _horario(cfg["horario"]) or CONFIG_PADRAO["horario"]
    for chave in ("limite_importacao", "limite_sobressalente", "limite_lote"):
        cfg[chave] = _inteiro(cfg[chave], CONFIG_PADRAO[chave], LIMITE_MIN, LIMITE_MAX)
    cfg["manter"] = _inteiro(cfg["manter"], CONFIG_PADRAO["manter"], MANTER_MIN, MANTER_MAX)
    cfg["pasta_extra"] = (cfg["pasta_extra"] or "").strip()
    return cfg


def _horario(texto) -> str:
    """"23:00" válido, ou "" quando não é uma hora."""
    try:
        return datetime.strptime(str(texto or "").strip()[:5], "%H:%M").strftime("%H:%M")
    except ValueError:
        return ""


def _inteiro(valor, padrao, minimo, maximo) -> int:
    """Valor inválido cai no padrão em vez de desligar a rotina em silêncio."""
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return int(padrao)


def salvar_config(valores: dict) -> None:
    """Grava só as chaves conhecidas, validando antes.

    A pasta extra é conferida NA HORA DE SALVAR (e criada se não existir):
    descobrir que o caminho estava errado no dia em que o backup era preciso
    é tarde demais."""
    limpos = {}
    if "ativo" in valores:
        limpos["ativo"] = "1" if str(valores["ativo"]) in ("1", "on", "true") else "0"
    if "horario" in valores:
        limpos["horario"] = _horario(valores["horario"])
        if not limpos["horario"]:
            raise ValueError("Informe o horário do backup no formato 23:00.")
    for chave in ("limite_importacao", "limite_sobressalente", "limite_lote"):
        if chave in valores:
            limpos[chave] = str(_inteiro(valores[chave], CONFIG_PADRAO[chave], LIMITE_MIN, LIMITE_MAX))
    if "manter" in valores:
        limpos["manter"] = str(_inteiro(valores["manter"], CONFIG_PADRAO["manter"],
                                        MANTER_MIN, MANTER_MAX))
    if "pasta_extra" in valores:
        limpos["pasta_extra"] = _validar_pasta(str(valores["pasta_extra"] or "").strip())

    with db() as conn:
        for chave, valor in limpos.items():
            conn.execute(
                "INSERT INTO backup_config (chave, valor) VALUES (?, ?) "
                "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                (chave, valor))


def _validar_pasta(caminho: str) -> str:
    if not caminho:
        return ""
    if os.path.isfile(caminho):
        raise ValueError(f"“{caminho}” é um arquivo, não uma pasta. "
                         "Informe a pasta onde as cópias devem ser gravadas.")
    try:
        os.makedirs(caminho, exist_ok=True)
        # Escrever de verdade é o único teste que vale: pasta de rede e pasta
        # sincronizada às vezes existem e mesmo assim recusam gravação.
        teste = os.path.join(caminho, ".kit_backup_teste")
        with open(teste, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(teste)
    except OSError as e:
        raise ValueError(f"Não consegui gravar em “{caminho}”: {e}. "
                         "Confira se o caminho existe e se a pasta aceita gravação "
                         "(pasta de rede desconectada é a causa mais comum).")
    return caminho


# ── Onde as cópias moram ─────────────────────────────────────────────────
def pasta_padrao() -> str:
    """A mesma pasta que o backup de migração já usa — uma só, pra não
    espalhar cópia do banco por dois lugares diferentes."""
    path = os.path.abspath(_get_db_path())
    return os.path.join(os.path.dirname(path) or ".", "backups")


# ── Fazer a cópia ────────────────────────────────────────────────────────
def criar_backup(motivo: str = "manual", user_id: int | None = None, descricao: str = "") -> dict:
    """Copia o banco pra pasta padrão (e pra extra, se houver), registra e
    limpa as cópias que passaram do limite.

    Devolve o registro criado. Erro na pasta EXTRA não invalida o backup: a
    cópia local já existe e é melhor guardá-la avisando do problema do que
    desistir das duas."""
    origem = os.path.abspath(_get_db_path())
    if origem.endswith(":memory:") or not os.path.exists(origem):
        raise ValueError("Banco em memória ou inexistente — nada a copiar.")

    cfg = get_config()
    destino_dir = pasta_padrao()
    os.makedirs(destino_dir, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"{os.path.basename(origem)}.{carimbo}.bak"
    # Duas cópias no mesmo segundo (antes e depois de um lote rápido) não
    # podem cair no mesmo arquivo: a segunda apagaria a primeira.
    n = 1
    while os.path.exists(os.path.join(destino_dir, nome)):
        n += 1
        nome = f"{os.path.basename(origem)}.{carimbo}_{n}.bak"
    destino = os.path.join(destino_dir, nome)

    _copiar_consistente(origem, destino)
    tamanho = os.path.getsize(destino)

    caminho_extra, erro = "", ""
    if cfg["pasta_extra"]:
        try:
            os.makedirs(cfg["pasta_extra"], exist_ok=True)
            caminho_extra = os.path.join(cfg["pasta_extra"], nome)
            shutil.copy2(destino, caminho_extra)
        except OSError as e:
            caminho_extra, erro = "", f"Cópia na pasta extra falhou: {e}"

    with db() as conn:
        rid = conn.execute(
            "INSERT INTO backup_registro (criado_em, arquivo, caminho, caminho_extra, "
            "tamanho, motivo, erro, criado_por, descricao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now_brt(), nome, destino, caminho_extra, tamanho, motivo, erro, user_id, descricao or None)
        ).lastrowid

    protegidas = _copias_protegidas()
    _limpar_antigos(destino_dir, cfg["manter"], protegidas)
    if cfg["pasta_extra"]:
        _limpar_antigos(cfg["pasta_extra"], cfg["manter"], protegidas)

    return {"id": rid, "arquivo": nome, "caminho": destino, "caminho_extra": caminho_extra,
            "tamanho": tamanho, "motivo": motivo, "erro": erro}


def _copias_protegidas() -> set[str]:
    """Arquivos que o rodízio não toca: os de migração (respondem "o problema
    começou na atualização?") e os de antes/depois de grande mudança."""
    marcas = ",".join("?" * len(MOTIVOS_PROTEGIDOS))
    with db() as conn:
        return {r["arquivo"] for r in conn.execute(
            f"SELECT arquivo FROM backup_registro WHERE motivo IN ({marcas})", MOTIVOS_PROTEGIDOS).fetchall()}


def eh_grande(tipo: str, quantidade: int) -> bool:
    """`tipo`: importacao, sobressalente ou lote."""
    return int(quantidade or 0) >= get_config()[f"limite_{tipo}"]


def linhas_xlsx(conteudo: bytes) -> int:
    """Linhas com dados na planilha (sem contar a primeira). 0 se não abrir."""
    import io
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        total = sum(1 for ws in wb.worksheets for l in ws.iter_rows(values_only=True)
                    if any(c not in (None, "") for c in l))
        wb.close()
        return max(0, total - 1)
    except Exception:
        return 0


def backup_de_grande_mudanca(momento: str, tipo: str, quantidade: int, descricao: str,
                             user_id: int | None) -> None:
    """Cópia de `momento` ("antes" ou "depois"). Se falhar, NÃO impede a
    operação: fica registrada com o erro, em vermelho, na tela de Backup."""
    motivo = f"grande_{momento}"
    texto = f"{descricao} — {quantidade} {_UNIDADES.get(tipo, 'itens')}"
    try:
        criar_backup(motivo, user_id, texto)
    except Exception as e:
        print(f"[KIT] Backup de grande mudanca ({momento}) falhou: {e}")
        with db() as conn:
            conn.execute(
                "INSERT INTO backup_registro (criado_em, arquivo, caminho, caminho_extra, tamanho, "
                "motivo, erro, criado_por, descricao) VALUES (?, '', '', '', 0, ?, ?, ?, ?)",
                (now_brt(), motivo, f"A cópia falhou: {e}", user_id, texto))


def _copiar_consistente(origem: str, destino: str) -> None:
    """Backup online do SQLite: sai íntegro mesmo com o sistema em uso."""
    org = sqlite3.connect(origem)
    try:
        dst = sqlite3.connect(destino)
        try:
            with dst:
                org.backup(dst)
        finally:
            dst.close()
    finally:
        org.close()


def _limpar_antigos(pasta: str, manter: int, protegidas: set[str] | None = None) -> list[str]:
    """Deixa só as `manter` cópias mais novas. Olha o disco, não o registro:
    é o disco que enche, e cópia apagada à mão não pode virar rombo na conta."""
    protegidas = protegidas or set()
    try:
        arquivos = [os.path.join(pasta, n) for n in os.listdir(pasta)
                    if n.endswith(".bak") and n not in protegidas]
    except OSError:
        return []
    arquivos.sort(key=lambda a: os.path.getmtime(a), reverse=True)
    apagados = []
    for velho in arquivos[manter:]:
        try:
            os.remove(velho)
            apagados.append(velho)
        except OSError:
            pass
    return apagados


# ── Estado, pra tela e pro laço automático ───────────────────────────────
def ultimo() -> dict | None:
    with db() as conn:
        r = conn.execute(
            "SELECT * FROM backup_registro WHERE arquivo <> '' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def listar(limite: int = 60) -> list[dict]:
    """As cópias registradas, com a informação de se o arquivo ainda existe —
    alguém pode ter apagado a pasta por fora, e a tela precisa dizer isso."""
    with db() as conn:
        rows = conn.execute(
            "SELECT b.*, u.nome AS criado_por_nome FROM backup_registro b "
            "LEFT JOIN users u ON u.id = b.criado_por "
            "ORDER BY b.id DESC LIMIT ?", (limite,)).fetchall()
    saida = []
    for r in rows:
        d = dict(r)
        d["existe"] = os.path.exists(d["caminho"] or "")
        d["existe_extra"] = os.path.exists(d["caminho_extra"] or "")
        d["protegida"] = d["motivo"] in MOTIVOS_PROTEGIDOS
        d["motivo_texto"] = MOTIVOS.get(d["motivo"], d["motivo"] or "—")
        d["tamanho_mb"] = round((d["tamanho"] or 0) / (1024 * 1024), 2)
        saida.append(d)
    return saida


def _para_datetime(texto: str | None) -> datetime | None:
    try:
        return datetime.strptime((texto or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def uso_protegidas() -> dict:
    """Quantas cópias que não se apagam existem e quanto ocupam, pra tela."""
    marcas = ",".join("?" * len(MOTIVOS_PROTEGIDOS))
    with db() as conn:
        linhas = conn.execute(f"SELECT caminho, tamanho FROM backup_registro WHERE motivo IN ({marcas}) "
                              "AND arquivo <> ''", MOTIVOS_PROTEGIDOS).fetchall()
    presentes = [l for l in linhas if os.path.exists(l["caminho"] or "")]
    return {"copias": len(presentes), "gb": round(sum(l["tamanho"] or 0 for l in presentes) / 1073741824, 2)}


def _ultimo_automatico() -> datetime | None:
    with db() as conn:
        r = conn.execute("SELECT criado_em FROM backup_registro WHERE motivo = 'automatico' "
                         "AND arquivo <> '' ORDER BY id DESC LIMIT 1").fetchone()
    return _para_datetime(r["criado_em"]) if r else None


def _horario_que_passou(agora: datetime, horario: str) -> datetime:
    """O último horário de backup já alcançado: hoje, ou ontem se ainda não deu a hora."""
    h, m = map(int, horario.split(":"))
    hoje = agora.replace(hour=h, minute=m, second=0, microsecond=0)
    return hoje if agora >= hoje else hoje - timedelta(days=1)


def precisa_agora() -> bool:
    """O laço automático pergunta isto a cada minuto.

    Há backup pendente quando não existe cópia automática DEPOIS do último
    horário que já passou. Assim sai às 23:00 e, com o servidor desligado na
    hora, sai quando ele voltar. Cópia manual, de migração ou de grande
    mudança não conta: a diária é uma rotina à parte."""
    cfg = get_config()
    if not cfg["ativo"]:
        return False
    ult = _ultimo_automatico()
    return ult is None or ult < _horario_que_passou(_agora(), cfg["horario"])


def proximo_previsto() -> datetime | None:
    """Quando o próximo backup automático deve sair. None = rotina desligada."""
    cfg = get_config()
    if not cfg["ativo"]:
        return None
    if precisa_agora():
        return _agora()
    return _horario_que_passou(_agora(), cfg["horario"]) + timedelta(days=1)


def _agora() -> datetime:
    return _para_datetime(now_brt()) or datetime.now()
