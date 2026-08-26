"""Encerrar o login sozinho depois de um tempo parado.

O problema é de chão de fábrica, não de segurança abstrata: o operador sai da
estação com o login aberto, o próximo senta e bipa no usuário de quem saiu.
Depois, quando alguém pergunta "quem montou este kit?", o histórico responde o
nome errado — e não há como descobrir.

Duas decisões que valem a explicação:

1. O relógio mora no SERVIDOR, não no cookie. Cookie o navegador guarda e o
   usuário pode mexer; e, principalmente, a bipagem conversa por WebSocket,
   que não consegue reescrever cookie no meio da conexão — quem estivesse
   bipando há vinte minutos seria deslogado justamente por estar trabalhando.
   Com o relógio aqui, tanto a navegação quanto cada bipagem renovam o tempo.

2. Reiniciar o servidor NÃO desloga ninguém: sessão que ele não conhece entra
   no mapa como "ativa agora". A alternativa — tratar desconhecido como
   expirado — derrubaria o galpão inteiro a cada atualização do sistema, e o
   que se ganharia é um caso de borda (uma sessão sobrevive um ciclo a mais)
   contra um prejuízo real e diário.
"""

import time

from database import db

CONFIG_PADRAO = {
    # Minutos parado até o login cair. 0 = desligado (ninguém é deslogado).
    "minutos": "0",
}
MINUTOS_MIN, MINUTOS_MAX = 1, 24 * 60      # de 1 minuto a 24 horas

# {sid: instante do último uso}. sid é sorteado no login e vive no cookie de
# sessão — é o que liga "esta aba" a "este relógio".
_ultimo_uso: dict[str, float] = {}


def get_config() -> dict:
    cfg = dict(CONFIG_PADRAO)
    with db() as conn:
        for r in conn.execute("SELECT chave, valor FROM login_config").fetchall():
            if r["chave"] in cfg:
                cfg[r["chave"]] = r["valor"]
    try:
        m = int(cfg["minutos"])
    except (TypeError, ValueError):
        m = 0
    cfg["minutos"] = 0 if m <= 0 else max(MINUTOS_MIN, min(MINUTOS_MAX, m))
    cfg["ativo"] = cfg["minutos"] > 0
    return cfg


def salvar_config(minutos) -> int:
    """Grava o limite. Devolve o valor efetivo (0 = desligado)."""
    try:
        m = int(minutos)
    except (TypeError, ValueError):
        m = 0
    m = 0 if m <= 0 else max(MINUTOS_MIN, min(MINUTOS_MAX, m))
    with db() as conn:
        conn.execute(
            "INSERT INTO login_config (chave, valor) VALUES ('minutos', ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (str(m),))
    return m


def limite_segundos() -> int:
    """0 quando a regra está desligada — o chamador nem consulta o relógio."""
    return get_config()["minutos"] * 60


def tocar(sid: str) -> None:
    """Marca que esta sessão foi usada agora. Chamado pela navegação e por
    cada bipagem: as duas são uso, e só uma delas passa por HTTP."""
    if sid:
        _ultimo_uso[sid] = time.monotonic()


def expirou(sid: str, limite_seg: int) -> bool:
    if not sid or limite_seg <= 0:
        return False
    visto = _ultimo_uso.get(sid)
    if visto is None:
        # Sessão de antes do restart: adota agora como início em vez de
        # derrubar quem está no meio de um kit.
        tocar(sid)
        return False
    return (time.monotonic() - visto) > limite_seg


def esquecer(sid: str) -> None:
    _ultimo_uso.pop(sid, None)


def _limpar_esquecidas(limite_seg: int) -> None:
    """Tira do mapa quem já passou muito do limite — senão ele cresce a cada
    login e nunca encolhe (um sid por aba, por dia, pra sempre)."""
    if limite_seg <= 0:
        return
    corte = time.monotonic() - (limite_seg * 4)
    for sid in [s for s, t in _ultimo_uso.items() if t < corte]:
        _ultimo_uso.pop(sid, None)
