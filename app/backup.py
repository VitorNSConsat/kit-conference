"""Cópia de segurança do banco — a que roda sozinha, de tempo em tempo.

O sistema já copiava o banco antes de mudar a estrutura
(database._backup_antes_de_migrar), mas isso é raro por natureza: só acontece
quando sai uma versão que mexe em tabela. Entre uma migração e outra o banco
podia passar meses sem uma cópia — e é justamente nesse meio que estão as
bipagens do dia, os kits, o estoque e a auditoria.

Aqui a cópia é por RELÓGIO: de X em X horas, enquanto o sistema estiver no ar.
Não existe agendador do Windows no meio, nem nada pra alguém lembrar de rodar.

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
    # De quantas em quantas horas. 24 = uma vez por dia.
    "intervalo_horas": "24",
    # Quantas cópias ficam guardadas. As mais antigas somem sozinhas.
    "manter": "30",
    # Segunda pasta (opcional). Vazio = só a pasta padrão.
    "pasta_extra": "",
}

INTERVALO_MIN, INTERVALO_MAX = 1, 720      # de 1 hora a 30 dias
MANTER_MIN, MANTER_MAX = 1, 999

MOTIVOS = {
    "automatico": "Automático",
    "manual": "Manual",
    "migracao": "Antes de migração",
}


# ── Configuração ─────────────────────────────────────────────────────────
def get_config() -> dict:
    cfg = dict(CONFIG_PADRAO)
    with db() as conn:
        for r in conn.execute("SELECT chave, valor FROM backup_config").fetchall():
            if r["chave"] in cfg:
                cfg[r["chave"]] = r["valor"]
    cfg["ativo"] = 1 if str(cfg["ativo"]) == "1" else 0
    cfg["intervalo_horas"] = _inteiro(cfg["intervalo_horas"], CONFIG_PADRAO["intervalo_horas"],
                                      INTERVALO_MIN, INTERVALO_MAX)
    cfg["manter"] = _inteiro(cfg["manter"], CONFIG_PADRAO["manter"], MANTER_MIN, MANTER_MAX)
    cfg["pasta_extra"] = (cfg["pasta_extra"] or "").strip()
    return cfg


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
    if "intervalo_horas" in valores:
        limpos["intervalo_horas"] = str(_inteiro(
            valores["intervalo_horas"], CONFIG_PADRAO["intervalo_horas"],
            INTERVALO_MIN, INTERVALO_MAX))
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
def criar_backup(motivo: str = "manual", user_id: int | None = None) -> dict:
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
            "tamanho, motivo, erro, criado_por) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now_brt(), nome, destino, caminho_extra, tamanho, motivo, erro, user_id)
        ).lastrowid

    protegidas = _copias_de_migracao()
    _limpar_antigos(destino_dir, cfg["manter"], protegidas)
    if cfg["pasta_extra"]:
        _limpar_antigos(cfg["pasta_extra"], cfg["manter"], protegidas)

    return {"id": rid, "arquivo": nome, "caminho": destino, "caminho_extra": caminho_extra,
            "tamanho": tamanho, "motivo": motivo, "erro": erro}


def _copias_de_migracao() -> set[str]:
    """As cópias feitas antes de uma mudança de estrutura não entram no rodízio.

    São raras (uma por versão que mexe em tabela) e marcam o "como estava antes
    desta versão" — a única cópia que responde "o problema começou na
    atualização?". Perder isso pro relógio, junto com as diárias, seria trocar
    a mais informativa pela mais recente."""
    with db() as conn:
        return {r["arquivo"] for r in conn.execute(
            "SELECT arquivo FROM backup_registro WHERE motivo = 'migracao'").fetchall()}


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
            "SELECT * FROM backup_registro ORDER BY id DESC LIMIT 1").fetchone()
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
        d["motivo_texto"] = MOTIVOS.get(d["motivo"], d["motivo"] or "—")
        d["tamanho_mb"] = round((d["tamanho"] or 0) / (1024 * 1024), 2)
        saida.append(d)
    return saida


def _para_datetime(texto: str | None) -> datetime | None:
    try:
        return datetime.strptime((texto or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def proximo_previsto() -> datetime | None:
    """Quando o próximo backup automático deve sair. None = rotina desligada."""
    cfg = get_config()
    if not cfg["ativo"]:
        return None
    ult = ultimo()
    quando = _para_datetime(ult["criado_em"]) if ult else None
    if quando is None:
        return _agora()          # nunca houve backup: o próximo é já
    return quando + timedelta(hours=cfg["intervalo_horas"])


def precisa_agora() -> bool:
    """O laço automático pergunta isto de tempos em tempos.

    A conta é sempre "quanto tempo passou desde a última cópia", nunca "que
    horas são" — assim um servidor que passou o fim de semana desligado faz o
    backup assim que volta, em vez de pular a janela e esperar a próxima."""
    previsto = proximo_previsto()
    return previsto is not None and _agora() >= previsto


def _agora() -> datetime:
    return _para_datetime(now_brt()) or datetime.now()
