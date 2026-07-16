# Design: Validação de Kits (Auditoria de Entrega)

**Data:** 2026-07-16  
**Status:** Aprovado

## Contexto

O sistema já realiza a conferência de kits (bipagem de itens). Após a conferência, o kit recebe uma etiqueta física com QR code. O objetivo desta feature é permitir que usuários internos **validem o kit pelo celular** ao escanear o QR, registrando quem auditou e quando — criando prova de entrega para uso no faturamento.

---

## Banco de Dados

Nova tabela `kit_validacoes` — sem alterar tabelas existentes:

```sql
CREATE TABLE IF NOT EXISTS kit_validacoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id      TEXT    NOT NULL REFERENCES kit_record(kit_id),
    validado_por INTEGER NOT NULL REFERENCES users(id),
    validado_em TEXT    NOT NULL,
    observacao  TEXT
);
```

---

## Fluxo Mobile — `/kit/{kit_id}`

A página já existe (pública, sem login). Mudanças:

1. **Usuário não logado**: exibe botão "🔒 Entrar para validar" → `/login` (o sistema de login atual não suporta `?next=`; usuário volta ao início e re-escaneia o QR — aceitável para uso interno)
2. **Usuário logado**: exibe formulário com textarea de observação (opcional) + botão "✅ Confirmar Validação"
3. `POST /kit/{kit_id}/validar` → grava em `kit_validacoes` usando `now_brt()` → redireciona para `/kit/{kit_id}?ok=validado`
4. Lista de validações anteriores exibida abaixo do formulário (quem, quando, obs)

---

## Relatório de Validações — `/reports/validacoes`

Nova página, linkada a partir de `/reports` como card/aba separada.

**Filtros:** data_ini, data_fim, validado_por (user id)

**Tabela (uma linha por validação):**
- Data Kit · Template · Cliente · Veículo · Garagem · Operador Conferência · Validado Por · Data Validação · Observação

**Export:** botão "📥 Exportar Excel" → `GET /reports/validacoes/export`  
Gera `.xlsx` com openpyxl. Cada linha = uma validação. Colunas extras: itens do kit (nome · quantidade) concatenados.

---

## Badge em Relatórios Principais — `/reports`

Coluna "Validações" adicionada à tabela de kits existente:
- `✅ 2` → 2 validações registradas (clicável → `/kit/{kit_id}`)
- `⚠️ Nenhuma` → sem validação

---

## Arquivos Afetados

| Arquivo | Mudança |
|---|---|
| `database.py` | `CREATE TABLE kit_validacoes` no schema init |
| `app/validacoes.py` | Novo módulo: `registrar()`, `listar_por_kit()`, `listar_relatorio()` |
| `main.py` | Rotas: `POST /kit/{kit_id}/validar`, `GET /reports/validacoes`, `GET /reports/validacoes/export` |
| `templates/kit_detail.html` | Seção de validação + lista |
| `templates/reports_validacoes.html` | Nova página de relatório |
| `templates/reports.html` | Coluna "Validações" + link para nova página |

---

## Fora do Escopo

- Validação por usuários externos (sem login)
- Checklist item a item na validação
- Bloqueio de faturamento por falta de validação
- Assinatura digital
