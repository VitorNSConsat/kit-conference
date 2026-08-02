# Bipagem multi-operador — Design

## Contexto

Hoje, na tela mobile (`/mobile`), a lista "Sessões em Andamento" só mostra
as sessões que o próprio operador logado iniciou (`WHERE ss.operador_id = ?`
em `main.py::mobile_hub`). Não existe nenhuma outra trava: `GET /session/{id}`,
o WebSocket de bipagem e `POST /session/{id}/finalize` não verificam se o
usuário logado é o dono da sessão — qualquer operador logado que soubesse a
URL já conseguiria abrir e continuar bipando ou até finalizar o kit de
outro. O único obstáculo real é a **descoberta**: não há como um operador
achar as sessões abertas por outra pessoa a partir do mobile.

Cada bip vira uma linha em `scan_session_items`, mas essa tabela não
registra quem bipou — só a sessão (`sessao_id`), o código e o tipo. A tela
de bipagem (`session.html`) mostra um "feed de eventos" ao vivo, mas ele é
inteiramente client-side (JS reagindo às respostas do WebSocket) e começa
vazio a cada carregamento de página — não há nenhum estado persistido dos
itens já bipados vindo do servidor.

## Objetivo

Permitir que um segundo operador veja e continue a bipagem de um kit
iniciado por outro, registrando por item quem bipou cada um, e sinalizando
no sistema (nunca na etiqueta impressa) quando um kit teve mais de um
operador.

## Decisões (confirmadas com o usuário)

1. **Sem passo de "assumir sessão"** — o operador 2 abre a sessão de outro
   e qualquer bip feito a partir daí já é gravado com o `operador_id` dele
   automaticamente.
2. **Agrupado por operador** na tela de bipagem — não uma lista única com
   etiqueta por item.
3. **Indicador de multi-operador** aparece no `kit_detail.html` do kit já
   finalizado (não na esteira, não na etiqueta).
4. **Lista "outros kits em andamento" no mobile** mostra TODAS as sessões
   ativas de outros operadores, sem esconder as que já têm 2+ operadores
   trabalhando.
5. **Sem sincronização em tempo real entre abas** — se dois operadores
   estiverem com a tela aberta ao mesmo tempo, cada um só vê a bipagem do
   outro depois de recarregar a página (não existe hoje um mecanismo de
   broadcast entre conexões WebSocket; adicionar isso é bem mais complexo
   e fica fora deste escopo).
6. **Desfazer bipagem** (botão "Voltar Bipagem") continua podendo remover o
   último item independente de quem bipou — não fica restrito a "só quem
   bipou pode desfazer".

## Arquitetura

### 1. Schema

Nova coluna em `scan_session_items`:

```sql
ALTER TABLE scan_session_items ADD COLUMN operador_id INTEGER REFERENCES users(id);
```

Nullable — linhas antigas (bipadas antes desta feature) ficam sem
atribuição; isso é aceitável, pois o efeito é só não aparecerem agrupadas
num painel novo que não existia antes. Segue o padrão já usado no projeto:
adicionar a condição em `_backup_antes_de_migrar()` (`database.py`) antes
de rodar o `ALTER TABLE`.

### 2. `app/sessions.py` — threading do operador atual

As funções que inserem em `scan_session_items` passam a receber
`operador_id: int` (o operador **logado atualmente**, não
`session["operador_id"]`, que continua sendo "quem iniciou a sessão" e não
muda) e gravá-lo em cada `INSERT`:

- `register_scan(sessao_id, codigo_barra, item_tipo_id=None, operador_id=None)`
- `registrar_serial(sessao_id, serial_barra, operador_id=None)`
- `registrar_patrimonio_de_fixo(sessao_id, codigo_patrimonio, operador_id=None)`
- `confirmar_componente(sessao_id, codigo_barra, quantidades, operador_id=None)`
- `confirmar_substituicao(sessao_id, codigo_barra, motivo, operador_id=None)`
- `confirmar_quantidade(sessao_id, codigo_barra, quantidade, operador_id=None)`

(`operador_id=None` só por segurança de assinatura — todos os call sites em
`main.py` vão sempre passar o valor; sem operador atribuído a linha grava
`NULL`.)

Nova função:

```python
def listar_itens_por_operador(sessao_id: int) -> list[dict]:
    """Itens já bipados nesta sessão, agrupados por operador (quem bipou
    cada um), na ordem em que os operadores apareceram pela primeira vez.
    Usada pelo painel "Itens bipados" em session.html."""
```

Implementação: uma query em `scan_session_items` join `item_tipo` e `users`,
ordenada por `bipado_em`, depois agrupada em Python por `operador_id` (chave
`None` vira "Sem operador registrado" — caso de linhas antigas).

Nova função:

```python
def operadores_da_sessao(sessao_id: int) -> list[dict]:
    """Lista de operadores distintos que bipararam algo nesta sessão
    (id + nome), na ordem da primeira bipagem de cada um. Usada tanto no
    painel agrupado quanto para decidir se o kit teve multi-operador."""
```

### 3. `main.py`

- `ws_session`: já tem `user_id = session_data.get("user_id")` disponível.
  Passa esse valor como `operador_id=user_id` em todas as chamadas às
  funções acima (`identificar` → `register_scan`, scan direto →
  `register_scan`, serial → `registrar_serial`, patrimônio fixo →
  `registrar_patrimonio_de_fixo`, componente → `confirmar_componente`,
  substituição → `confirmar_substituicao`, quantidade →
  `confirmar_quantidade`).
- `session_page`: passa `itens_por_operador = sessions_mod.listar_itens_por_operador(sessao_id)`
  no contexto do template.
- `mobile_hub`: query adicional listando sessões ativas de **outros**
  operadores (`WHERE ss.operador_id != ? AND ss.status = 'em_andamento'`),
  mesmas colunas da query existente. Passa como `sessoes_outros` no
  contexto.
- `kit_detail` (rota pública por QR, `_resolver_kit_id`/handler existente):
  busca `operadores_da_sessao(kit["sessao_id"])`; se tiver 2+ operadores,
  passa a lista no contexto como `operadores_kit` (senão passa lista
  vazia/None — o template só renderiza a seção se houver 2+).

### 4. Templates

**`templates/mobile_hub.html`**
- Nova seção/bloco abaixo de "Sessões em Andamento" (própria): um botão
  "👥 Ver outros kits em andamento" que expande (ou navega a uma âncora)
  mostrando `sessoes_outros` — cada linha com veículo/kit/cliente, "iniciado
  por {{ operador_nome }}" e um link "▶️ Continuar bipagem" para
  `/session/{{ s.id }}`. Reaproveita o mesmo componente visual das sessões
  próprias, só com o operador de origem visível.

**`templates/session.html`**
- Novo painel "Itens Bipados" (cartão separado do feed ao vivo "Eventos de
  Bipagem"), renderizado a partir de `itens_por_operador`: um bloco por
  operador, cabeçalho "Bipado por {{ nome }} ({{ itens|length }})", lista
  dos itens (descrição + horário). Se só houver um operador, mostra só um
  bloco (sem ênfase extra — não muda o comportamento visual de sessões de
  operador único).

**`templates/kit_detail.html`**
- Se `operadores_kit` tiver 2+ entradas: nova seção "👥 Operadores
  envolvidos" com nome de cada um e quantos itens bipou. Não aparece se só
  teve um operador (comportamento atual, sem mudança visual pra maioria dos
  kits).

## Fora de escopo

- Sincronização em tempo real entre abas/conexões diferentes (decisão 5).
- Qualquer indicação de multi-operador na etiqueta impressa (ZPL ou HTML).
- Restringir quem pode desfazer a última bipagem.
- Qualquer trava de permissão nova para "continuar sessão de outro" — usa o
  mesmo `@require_login` que já protege essas rotas hoje.
