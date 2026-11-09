"""Trilha de auditoria.

Grava toda requisição que altera dados — de admin ou não. O registro é
feito por middleware, e não rota a rota, porque cobertura aqui é o
requisito: uma rota nova criada amanhã já nasce auditada, sem depender de
alguém lembrar de instrumentar.
"""

import json as _json
import re as _re

from database import db, now_brt
import app.datas as datas_mod
import app.filtros as filtros_mod

# Nunca guardar o valor destes campos no detalhe do log.
_CAMPOS_SENSIVEIS = {"password", "senha", "senha_atual", "nova_senha",
                     "confirmar_senha", "password_hash", "secret", "token"}

# Antes eram 2000: edição de kit com muitos itens era cortada no meio. Os
# registros antigos continuam cortados — a tela avisa.
_LIMITE_ANTIGO = 2000
_LIMITE_DETALHE = 20000
_LIMITE_CAMPO = 1000


def _resumir_form(form) -> str:
    """Serializa o formulário para o log, mascarando senhas e cortando
    conteúdo de arquivo (um .xlsx inteiro não vai para dentro do banco)."""
    partes = []
    # multi_items e não items(): items() devolve só o ÚLTIMO valor de cada
    # campo repetido — num pacote de sobressalentes com 5 itens, o log ficava
    # só com o último item e a última observação.
    for chave, valor in form.multi_items():
        if chave.lower() in _CAMPOS_SENSIVEIS:
            partes.append(f"{chave}=***")
            continue
        # UploadFile e afins: guarda só o nome do arquivo
        nome_arquivo = getattr(valor, "filename", None)
        if nome_arquivo is not None:
            partes.append(f"{chave}=<arquivo:{nome_arquivo}>")
            continue
        texto = str(valor)
        if len(texto) > _LIMITE_CAMPO:
            texto = texto[:_LIMITE_CAMPO] + "…"
        partes.append(f"{chave}={texto}")
    resumo = " | ".join(partes)
    return resumo[:_LIMITE_DETALHE]


def _resumir_json(corpo: bytes) -> str:
    """Mesma ideia de _resumir_form, para rotas que recebem JSON (a API do
    Portal, por exemplo) — mascara os mesmos campos sensíveis."""
    try:
        dados = _json.loads(corpo)
    except Exception:
        return ""
    if not isinstance(dados, dict):
        return ""
    partes = []
    for chave, valor in dados.items():
        if chave.lower() in _CAMPOS_SENSIVEIS:
            partes.append(f"{chave}=***")
            continue
        texto = str(valor)
        if len(texto) > _LIMITE_CAMPO:
            texto = texto[:_LIMITE_CAMPO] + "…"
        partes.append(f"{chave}={texto}")
    resumo = " | ".join(partes)
    return resumo[:_LIMITE_DETALHE]


def registrar(user_id: int | None, user_nome: str | None, acao: str,
              metodo: str, caminho: str, detalhe: str = "",
              ip: str = "", status: int | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO auditoria "
            "(user_id, user_nome, acao, metodo, caminho, detalhe, ip, status, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, user_nome, acao, metodo, caminho,
             detalhe or None, ip or None, status, now_brt())
        )


def classificar(caminho: str) -> str:
    """Rótulo legível da ação, derivado da rota — o que aparece na tela."""
    c = caminho.lower()
    if "/delete" in c or "/excluir" in c or "/remover" in c:
        return "EXCLUSAO"
    if "/login" in c:
        return "LOGIN"
    if "/logout" in c:
        return "LOGOUT"
    if "/finalize" in c:
        return "FINALIZACAO DE KIT"
    if "/cancel" in c:
        return "CANCELAMENTO"
    if "/import" in c or "/importar" in c:
        return "IMPORTACAO"
    if "/usuarios" in c:
        return "GESTAO DE USUARIOS"
    if "/scan" in c or "/session" in c:
        return "BIPAGEM"
    # Antes de /toggle: a rota de vínculo não é toggle, mas convém ficar
    # explícita no log por ser mudança rastreável de kit ↔ veículo.
    if c.startswith("/kit-record/") and c.endswith("/veiculo"):
        return "VINCULO DE VEICULO"
    if "/toggle" in c:
        return "ALTERACAO DE STATUS"
    if "/admin/producao/" in c:
        if "/nota-fiscal" in c:
            return "PRODUCAO: NOTA FISCAL"
        if c.endswith("/transito"):
            return "PRODUCAO: EM TRANSITO"
        if "/cliente-instalando" in c:
            return "PRODUCAO: CHEGADA NO CLIENTE"
        if "/cliente-concluido" in c:
            return "PRODUCAO: INSTALACAO CONCLUIDA"
        if "/voltar" in c:
            return "PRODUCAO: VOLTAR ESTAGIO"
    if "/mover-garagem" in c:
        return "VEICULOS: MUDANCA DE GARAGEM"
    return "ALTERACAO"


def _filtros(data_ini: str, data_fim: str, user_id: str, acao: str,
             caminho_prefixo: str, busca: str = "") -> tuple[str, list]:
    """WHERE compartilhado pela contagem e pela listagem — se os dois
    divergirem, o total diz um número e a lista entrega outro.

    A busca por texto entra AQUI, no SQL, e não em Python depois: filtrando
    depois seria preciso carregar tudo antes de paginar, que é exatamente o
    que obrigava o teto de 2000 e escondia o resto."""
    where = "WHERE 1=1"
    sql_data, params = datas_mod.clausula("criado_em", data_ini, data_fim)
    where += sql_data
    # Os dois aceitam lista (filtro de múltipla escolha) ou um valor só —
    # as telas antigas e os links salvos continuam mandando um valor.
    ids = [int(u) for u in filtros_mod.lista(
        user_id if isinstance(user_id, (list, tuple)) else [user_id])
        if str(u).isdigit()]
    sql_u, p_u = filtros_mod.em("user_id", ids)
    where += sql_u
    params += p_u
    acoes = filtros_mod.lista(acao if isinstance(acao, (list, tuple)) else [acao])
    sql_a, p_a = filtros_mod.em("acao", acoes)
    where += sql_a
    params += p_a
    if caminho_prefixo:
        where += " AND caminho LIKE ?"
        params.append(caminho_prefixo + "%")
    # Cada palavra tem que aparecer em algum dos campos (ordem não importa),
    # mesma regra da busca das outras telas.
    for palavra in _sem_acento_py(busca).split():
        extra_sql, extra_params = _busca_pelo_que_a_tela_mostra(palavra)
        where += (" AND (sem_acento(COALESCE(detalhe,'') || ' ' || COALESCE(user_nome,'')"
                  " || ' ' || COALESCE(acao,'') || ' ' || COALESCE(caminho,'')"
                  " || ' ' || COALESCE(ip,'')) LIKE ?" + extra_sql + ")")
        params.append(f"%{palavra}%")
        params += extra_params
    return where, params


def _busca_pelo_que_a_tela_mostra(palavra: str) -> tuple[str, list]:
    """O registro guarda a rota e os ids; a tela mostra o título e os nomes.
    Sem isto, buscar "Parafuso" ou "sobressalentes" não achava o que estava
    escrito na própria linha. Traduz a palavra de volta pra rota e pros ids."""
    sql, params = "", []
    for modelo, titulo in _TITULOS.items():
        if palavra in _sem_acento_py(titulo):
            sql += " OR caminho LIKE ?"
            params.append(_re.sub(r"\{\w+\}", "%", modelo))
    consultas = (
        ("estoque_id", "SELECT e.id FROM estoque e JOIN item_tipo it ON it.id = e.item_tipo_id "
                       "WHERE sem_acento(it.nome || ' ' || e.codigo_barra) LIKE ? LIMIT 60"),
        ("item_tipo_id", "SELECT id FROM item_tipo WHERE sem_acento(nome) LIKE ? LIMIT 60"),
        ("veiculo_id", "SELECT id FROM veiculos WHERE sem_acento(numero) LIKE ? LIMIT 60"),
    )
    with db() as conn:
        for chave, consulta in consultas:
            for (i,) in conn.execute(consulta, (f"%{palavra}%",)).fetchall():
                # " | " nas pontas: estoque_id=1 não pode casar com estoque_id=12.
                sql += " OR (' | ' || COALESCE(detalhe,'') || ' | ') LIKE ? OR (' | ' || COALESCE(detalhe,'') || ' | ') LIKE ?"
                params += [f"% | {chave}={i} | %", f"% | {chave}_%={i} | %"]
    return sql, params


def _sem_acento_py(texto: str) -> str:
    import unicodedata
    t = str(texto or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn")


def contar(data_ini: str = "", data_fim: str = "", user_id: str = "",
           acao: str = "", caminho_prefixo: str = "", busca: str = "") -> int:
    """Quantos registros batem com o filtro — o total DE VERDADE, sem o
    teto da listagem. É ele que a tela mostra e que dimensiona a paginação."""
    where, params = _filtros(data_ini, data_fim, user_id, acao, caminho_prefixo, busca)
    with db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM auditoria {where}", params).fetchone()[0]


def listar(data_ini: str = "", data_fim: str = "", user_id: str = "",
           acao: str = "", limite: int = 500, caminho_prefixo: str = "",
           offset: int = 0, busca: str = "") -> list[dict]:
    """`limite`/`offset` servem a paginação, não a um corte escondido: a
    tela pede uma página por vez e usa contar() pro total. Antes a tela
    pedia 2000 de uma vez e paginava esse pedaço — passando disso, os
    registros mais antigos do período simplesmente não existiam pra quem
    olhava, sem nenhum aviso."""
    where, params = _filtros(data_ini, data_fim, user_id, acao, caminho_prefixo, busca)
    query = f"SELECT * FROM auditoria {where} ORDER BY id DESC LIMIT ? OFFSET ?"
    params = params + [int(limite), int(offset)]
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def resumos_para_analise(data_ini: str = "", data_fim: str = "", user_id: str = "",
                         acao: str = "", busca: str = "") -> dict:
    """Cortes agregados do log, calculados NO BANCO — por dia, por usuário,
    por ação, por dia×usuário e por hora do dia.

    São GROUP BY sobre o mesmo WHERE da listagem, não contagem em Python
    sobre as linhas carregadas: assim os totais valem pro período inteiro
    mesmo quando ele tem centenas de milhares de registros, e a exportação
    não precisa trazer tudo pra memória só pra somar."""
    where, params = _filtros(data_ini, data_fim, user_id, acao, "", busca)

    def agrupar(expressao, rotulos, ordem):
        with db() as conn:
            rows = conn.execute(
                f"SELECT {expressao}, COUNT(*) AS total "
                f"FROM auditoria {where} GROUP BY {rotulos} ORDER BY {ordem}",
                params).fetchall()
        return [dict(r) for r in rows]

    return {
        "por_dia": agrupar(
            "SUBSTR(criado_em, 1, 10) AS dia", "dia", "dia DESC"),
        "por_usuario": agrupar(
            "COALESCE(user_nome, '(sem usuário)') AS usuario, user_id",
            "usuario, user_id", "total DESC"),
        "por_acao": agrupar("acao", "acao", "total DESC"),
        "por_dia_usuario": agrupar(
            "SUBSTR(criado_em, 1, 10) AS dia, COALESCE(user_nome, '(sem usuário)') AS usuario",
            "dia, usuario", "dia DESC, total DESC"),
        "por_hora": agrupar(
            "SUBSTR(criado_em, 12, 2) AS hora", "hora", "hora"),
        "por_status": agrupar(
            "COALESCE(status, 0) AS status", "status", "total DESC"),
    }


def acoes_distintas() -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT acao FROM auditoria ORDER BY acao"
        ).fetchall()
    return [r["acao"] for r in rows]


# ── Leitura: transformar o registro técnico em algo que o operador entende ────
# O registro em si continua cru (é a prova do que chegou ao servidor); aqui só
# se traduz na hora de mostrar — vale também pros registros antigos.

_ACOES_TEXTO = {
    "ALTERACAO": "Alteração", "EXCLUSAO": "Exclusão", "LOGIN": "Login", "LOGOUT": "Logout",
    "FINALIZACAO DE KIT": "Finalização de kit", "CANCELAMENTO": "Cancelamento",
    "IMPORTACAO": "Importação", "GESTAO DE USUARIOS": "Gestão de usuários", "BIPAGEM": "Bipagem",
    "VINCULO DE VEICULO": "Vínculo de veículo", "ALTERACAO DE STATUS": "Alteração de status",
    "PRODUCAO: NOTA FISCAL": "Produção: nota fiscal", "PRODUCAO: EM TRANSITO": "Produção: em trânsito",
    "PRODUCAO: CHEGADA NO CLIENTE": "Produção: chegada no cliente",
    "PRODUCAO: INSTALACAO CONCLUIDA": "Produção: instalação concluída",
    "PRODUCAO: VOLTAR ESTAGIO": "Produção: voltar estágio",
    "VEICULOS: MUDANCA DE GARAGEM": "Veículos: mudança de garagem",
}

_ACOES_COR = {"EXCLUSAO": "badge-danger", "CANCELAMENTO": "badge-warning",
              "FINALIZACAO DE KIT": "badge-success", "LOGIN": "aud-neutro", "LOGOUT": "aud-neutro"}

_TITULOS = {
    "/login": "Entrada no sistema", "/logout": "Saída do sistema",
    "/kit/{id}/conferir-item": "Conferência de item do kit",
    "/kit/{id}/desfazer-item": "Conferência de item desfeita",
    "/kit/{id}/validar": "Verificação do kit",
    "/kit/{id}/pre-conferencia": "Pré-conferência no kit pronto",
    "/session/start": "Início de bipagem", "/session/remessa": "Remessa escolhida na bipagem",
    "/session/{n}/destino": "Destino da bipagem definido",
    "/session/{n}/imprimir-pausa": "Etiqueta de andamento gerada",
    "/session/{n}/finalize": "Kit finalizado", "/session/{n}/cancel": "Bipagem cancelada",
    "/session/{n}/item/{n}/remover": "Bipagem de item excluída",
    "/session/{n}/trocar-modelo": "Modelo da bipagem trocado",
    "/print-queue/{n}/impresso": "Etiqueta impressa",
    "/print-queue/{n}/cancelar": "Etiqueta cancelada",
    "/print-queue/pausa/{n}/impresso": "Etiqueta de andamento impressa",
    "/print-queue/pausa/{n}/cancelar": "Etiqueta de andamento cancelada",
    "/admin/producao/transito": "Produção: envio em trânsito",
    "/admin/producao/nota-lote": "Nota fiscal em lote",
    "/admin/producao/cliente-instalando-lote": "Produção: chegada no cliente (lote)",
    "/admin/producao/{id}/cliente-instalando": "Produção: chegada no cliente",
    "/admin/producao/{id}/cliente-concluido": "Produção: instalação concluída",
    "/admin/producao/{id}/nota-fiscal": "Nota fiscal do kit",
    "/admin/producao/remessas/{n}/alvo": "Remessa: alvo alterado",
    "/admin/producao/remessas/{n}/adicionar": "Remessa: veículos adicionados",
    "/admin/producao/remessas/{n}/selecionados": "Remessa: veículos selecionados",
    "/admin/producao/remessas/{n}/editar": "Remessa editada",
    "/admin/producao/remessas/{n}/fechar": "Remessa fechada",
    "/admin/producao/remessas/{n}/reabrir": "Remessa reaberta",
    "/admin/producao/remessas/{n}/excluir": "Remessa excluída",
    "/admin/sobressalente": "Pacote de sobressalentes",
    "/admin/estoque": "Estoque cadastrado", "/admin/estoque/alertas": "Avisos de estoque configurados",
    "/admin/estoque/reconciliar-producao": "Correção do estoque de produção",
    "/admin/estoque/{n}/corrigir": "Correção de quantidade do estoque",
    "/admin/estoque/{n}/ajustar": "Ajuste de estoque",
    "/admin/estoque/{n}/minimo": "Mínimo do estoque alterado",
    "/admin/estoque/{n}/status-compra": "Status de compra alterado",
    "/admin/tipos/completo": "Novo item cadastrado", "/admin/tipos/{n}/renomear": "Item renomeado",
    "/admin/tipos/{n}/delete": "Item excluído",
    "/admin/tipos/{n}/set-codigo-fixo": "Código fixo do item alterado",
    "/admin/tipos/{n}/toggle-controle-externo": "Controle externo do item alterado",
    "/admin/tipos/{n}/toggle-reutilizavel": "Item reutilizável alterado",
    "/admin/tipos/{n}/toggle-exigir-mac-address": "Exigência de MAC do item alterada",
    "/admin/items": "Patrimônio cadastrado à mão", "/admin/items/{n}/delete": "Patrimônio excluído",
    "/admin/items/patrimonio/{c}": "Correção de patrimônio",
    "/admin/patrimonios/importar": "Leitura de planilha de patrimônios",
    "/admin/patrimonios/importar/confirmar": "Importação de patrimônios",
    "/admin/templates": "Novo kit/pedido", "/admin/templates/import-pedido": "Importação de pedido",
    "/admin/templates/import-bom": "Kit criado pela BOM",
    "/admin/templates/{n}/edit": "Edição de kit/pedido",
    "/admin/templates/{n}/toggle": "Kit/pedido ativado ou desativado",
    "/admin/templates/{n}/toggle-concluido": "Kit/pedido marcado como concluído",
    "/admin/templates/{n}/nova-versao": "Nova versão de kit/pedido",
    "/admin/templates/{n}/delete": "Exclusão de kit/pedido",
    "/admin/templates/{n}/unidades": "Unidades do pedido",
    "/admin/templates/{n}/conjunto/verificar-em-conjunto": "Regra de verificação do conjunto",
    "/admin/veiculos": "Veículo cadastrado", "/admin/veiculos/import": "Importação de veículos",
    "/admin/veiculos/excluir-em-massa": "Exclusão de veículos em massa",
    "/admin/veiculos/{n}/editar": "Edição de veículo", "/admin/veiculos/{n}/painel": "Alteração pelo painel do veículo",
    "/admin/veiculos/{n}/modelo": "Modelo do veículo alterado", "/admin/veiculos/{n}/regiao": "Região do veículo alterada",
    "/admin/veiculos/{n}/liberar": "Veículo liberado", "/admin/veiculos/{n}/delete": "Veículo excluído",
    "/admin/veiculos/{n}/desativar": "Veículo desativado", "/admin/veiculos/{n}/reativar": "Veículo reativado",
    "/admin/garagens": "Garagem cadastrada", "/admin/clientes/{n}/prefixo": "Prefixo do cliente alterado",
    "/admin/clientes/{n}/delete": "Cliente excluído",
    "/admin/usuarios": "Usuário cadastrado", "/admin/usuarios/inatividade": "Tempo de inatividade alterado",
    "/admin/usuarios/{n}/permissoes": "Permissões do usuário alteradas",
    "/admin/usuarios/{n}/admin": "Administrador do usuário alterado",
    "/admin/backup/config": "Configuração de backup", "/admin/backup/agora": "Backup manual",
    "/admin/hardware/{n}/acao": "Ação em RMA",
    "/reports/{id}/delete": "Kit excluído", "/reports/reprint/{id}": "Etiqueta reimpressa",
}

_ROTULOS = {
    "cliente": "Cliente", "nome": "Nome", "estoque_id": "Item do estoque", "item_tipo_id": "Tipo de item",
    "quantidade": "Quantidade", "qtd": "Quantidade", "observacao": "Observação",
    "observacao_pacote": "Observação do pacote", "codigo_barra": "Código", "quantidade_minima": "Mínimo",
    "quantidade_atual": "Quantidade atual", "motivo": "Motivo", "tipo": "Tipo", "componente_codigo": "Conjunto",
    "obrigatorio": "Obrigatório", "requer_serial": "Requer serial", "veiculo_id": "Veículo", "veiculo": "Veículo",
    "garagem": "Garagem", "modelo": "Modelo", "username": "Usuário", "password": "Senha",
    "numero_pedido": "Número do pedido", "arquivo": "Arquivo", "regiao": "Região", "nota_fiscal": "Nota fiscal",
    "kit_id": "Kit", "item_id": "Item", "codigos": "Códigos", "completar_zeros": "Completar com zeros",
}

_SIM_NAO = {"obrigatorio", "requer_serial", "reutilizavel", "ativo", "completar_zeros"}
_OCULTOS = {"voltar_para", "next", "_modo"}
_CHAVE = _re.compile(r"^[\w.\-\[\]]+=")
_INDICE = _re.compile(r"^(.+)_(\d+)$")


def acao_texto(acao: str) -> str:
    return _ACOES_TEXTO.get(acao or "", (acao or "—").capitalize())


def _rota_modelo(caminho: str) -> str:
    c = _re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}", "{id}", caminho or "")
    c = _re.sub(r"/patrimonio/.*", "/patrimonio/{c}", c)
    return _re.sub(r"/\d+(?=/|$)", "/{n}", c)


def _pares(detalhe: str) -> list[tuple[str, str]] | None:
    """[(chave, valor)] do "a=1 | b=2", ou None quando é frase livre ("6 kit(s):
    nota fiscal definida para 230"). Um " | " dentro de um valor não abre
    campo novo: o pedaço sem "chave=" volta pro valor anterior."""
    partes = (detalhe or "").split(" | ")
    if not partes or not _CHAVE.match(partes[0]):
        return None
    saida: list[list[str]] = []
    for p in partes:
        if _CHAVE.match(p):
            k, v = p.split("=", 1)
            saida.append([k, v])
        else:
            saida[-1][1] += " | " + p
    return [(k, v) for k, v in saida]


def _rotulo(chave: str) -> str:
    return _ROTULOS.get(chave, chave.replace("_", " ").capitalize())


def _mapas(registros: list[dict]) -> dict:
    """Nomes pros ids que aparecem nos registros, numa consulta por tabela."""
    ids = {"estoque_id": set(), "item_tipo_id": set(), "veiculo_id": set()}
    for r in registros:
        for k, v in _pares(r.get("detalhe") or "") or []:
            base = (_INDICE.match(k).group(1) if _INDICE.match(k) else k)
            if base in ids and v.isdigit():
                ids[base].add(int(v))

    def consulta(conn, sql, valores):
        valores = sorted(valores)
        if not valores:
            return {}
        return {str(a): b for a, b in conn.execute(sql.format(",".join("?" * len(valores))), valores).fetchall()}

    with db() as conn:
        return {
            "estoque_id": consulta(conn, "SELECT e.id, it.nome || ' (' || e.codigo_barra || ')' FROM estoque e "
                                         "JOIN item_tipo it ON it.id = e.item_tipo_id WHERE e.id IN ({})", ids["estoque_id"]),
            "item_tipo_id": consulta(conn, "SELECT id, nome FROM item_tipo WHERE id IN ({})", ids["item_tipo_id"]),
            "veiculo_id": consulta(conn, "SELECT id, numero FROM veiculos WHERE id IN ({})", ids["veiculo_id"]),
        }


def _valor(base: str, v: str, mapas: dict) -> str:
    if base in mapas and v in mapas[base]:
        return mapas[base][v]
    if base in _SIM_NAO and v in ("1", "on", "true"):
        return "Sim"
    return v


def interpretar(registros: list[dict]) -> list[dict]:
    """Acrescenta a cada registro o que a tela mostra: título em português,
    resumo, campos, tabela (campos repetidos ou numerados, um item por linha)
    e se havia observação (com/sem/None = o formulário nem tinha o campo)."""
    mapas = _mapas(registros)
    for r in registros:
        modelo = _rota_modelo(r.get("caminho") or "")
        r["titulo"] = _TITULOS.get(modelo) or acao_texto(r.get("acao"))
        r["acao_texto"] = acao_texto(r.get("acao"))
        r["acao_cor"] = _ACOES_COR.get(r.get("acao"), "badge-info")
        r["recusado"] = bool(r.get("status") and r["status"] >= 400)
        r["cortado"] = len(r.get("detalhe") or "") == _LIMITE_ANTIGO
        r.update(texto_livre=None, campos=[], tabela=None, obs=None, resumo="")
        pares = _pares(r.get("detalhe") or "")
        if pares is None:
            r["texto_livre"] = r.get("detalhe") or None
            r["resumo"] = r["texto_livre"] or ""
            continue
        pares = [(k, v) for k, v in pares if k not in _OCULTOS]
        repetidas = {k for k in (p[0] for p in pares) if sum(1 for x in pares if x[0] == k) > 1}
        linhas: dict = {}
        colunas: list[str] = []
        vistos: dict = {}
        for k, v in pares:
            m = _INDICE.match(k)
            if m or k in repetidas:
                base = m.group(1) if m else k
                vistos[k] = vistos.get(k, -1) + 1
                indice = int(m.group(2)) if m else vistos[k]
                if base not in colunas:
                    colunas.append(base)
                linhas.setdefault(indice, {})[base] = _valor(base, v, mapas)
            else:
                r["campos"].append({"chave": k, "rotulo": _rotulo(k), "valor": _valor(k, v, mapas)})
        if linhas:
            r["tabela"] = {"colunas": [_rotulo(c) for c in colunas],
                           "linhas": [[linhas[i].get(c, "") for c in colunas] for i in sorted(linhas)]}
        observacoes = [v for k, v in pares if (_INDICE.match(k).group(1) if _INDICE.match(k) else k) == "observacao"]
        if observacoes:
            r["obs"] = "com" if any(o.strip() for o in observacoes) else "sem"
        visiveis = [f"{c['rotulo']}: {c['valor']}" for c in r["campos"]
                    if c["valor"].strip() and c["valor"] != "***" and c["chave"] != "observacao"]
        if r["tabela"]:
            n = len(r["tabela"]["linhas"])
            visiveis.append(f"{n} {'item' if n == 1 else 'itens'}")
        r["resumo"] = " · ".join(visiveis[:4])
    return registros


def buscar_detalhado(registro_id: int) -> dict | None:
    """Um registro, interpretado. Num pacote de sobressalentes, junta o pacote
    criado naquele momento: registros antigos guardaram só o último item, mas
    o pacote em si tem todos, com a observação de cada um."""
    with db() as conn:
        linha = conn.execute("SELECT * FROM auditoria WHERE id = ?", (registro_id,)).fetchone()
    if not linha:
        return None
    r = interpretar([dict(linha)])[0]
    r["eh_sobressalente"] = r.get("caminho") == "/admin/sobressalente"
    r["pacote"] = None
    if r["eh_sobressalente"]:
        import app.sobressalentes as sobressalentes_mod
        cliente = next((c["valor"] for c in r["campos"] if c["chave"] == "cliente"), "")
        with db() as conn:
            achado = conn.execute(
                "SELECT id FROM sobressalente_pacote WHERE criado_por IS ? AND cliente = ? "
                "AND ABS(julianday(criado_em) - julianday(?)) * 86400 <= 15 "
                "ORDER BY ABS(julianday(criado_em) - julianday(?)) LIMIT 1",
                (r.get("user_id"), cliente, r["criado_em"], r["criado_em"])).fetchone()
        if achado:
            r["pacote"] = sobressalentes_mod.buscar(achado[0])
    return r
