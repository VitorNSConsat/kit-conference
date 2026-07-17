# Design: Cadastro de Veículos e Rastreamento de Kits

**Data:** 2026-07-17
**Status:** Aprovado

## Contexto

O sistema já grava `veiculo` e `garagem` como texto livre na finalização do kit. O objetivo é criar um cadastro formal de veículos (com importação por Excel), vinculá-los opcionalmente aos kits durante a finalização, e oferecer um painel mostrando o histórico de kits por veículo.

---

## Banco de Dados

Nova tabela `veiculos` — sem alterar tabelas existentes:

```sql
CREATE TABLE IF NOT EXISTS veiculos (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    numero   TEXT NOT NULL,
    cliente  TEXT NOT NULL,
    garagem  TEXT DEFAULT '',
    ativo    INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL
);
```

`kit_record` ganha uma coluna nova (nullable, sem breaking change):

```sql
ALTER TABLE kit_record ADD COLUMN veiculo_id INTEGER REFERENCES veiculos(id);
```

---

## Módulo `app/veiculos.py`

Funções:
- `listar(cliente=None, ativo=True) -> list[dict]`
- `buscar(id) -> dict | None`
- `criar(numero, cliente, garagem) -> int`
- `atualizar(id, numero, cliente, garagem)`
- `desativar(id)`
- `importar_excel(bytes) -> dict` — lê planilha com colunas `Número do Veículo` e `Cliente`, retorna `{inseridos, ignorados, erros}`
- `historico_kits(veiculo_id) -> list[dict]` — todos os kits vinculados com data, template, operador, verificado (bool)

---

## Rotas

| Método | URL | Descrição |
|--------|-----|-----------|
| GET | `/admin/veiculos` | Lista + filtro por cliente |
| POST | `/admin/veiculos` | Cadastro manual |
| GET | `/admin/veiculos/import` | Formulário de importação |
| POST | `/admin/veiculos/import` | Processa planilha Excel |
| GET | `/admin/veiculos/modelo.xlsx` | Baixa planilha modelo |
| GET | `/admin/veiculos/{id}` | Detalhe + histórico de kits |
| POST | `/admin/veiculos/{id}/editar` | Atualiza dados |
| POST | `/admin/veiculos/{id}/desativar` | Desativa veículo |
| POST | `/kit-record/{kit_id}/veiculo` | Vincula/desvincula veículo de um kit já finalizado |

Todas as rotas de admin requerem `@require_login`.

---

## Tela `/admin/veiculos`

- Tabela com colunas: **Número · Cliente · Garagem · Kits enviados · Último envio · Status**
- Filtro por cliente (select com clientes únicos dos templates)
- Badge de status: `✅ N kits` (verde) ou `⚠️ Nenhum` (cinza)
- Botão "＋ Novo Veículo" (formulário inline ou modal)
- Botão "📥 Importar Excel" → `/admin/veiculos/import`
- Botão "📄 Baixar Modelo" → `/admin/veiculos/modelo.xlsx`

---

## Tela `/admin/veiculos/{id}`

- Dados do veículo (editáveis via form)
- Tabela de histórico de kits: Data · Template · Operador · Verificado (✅/—)
- Botão "Desativar" (soft delete — veículo desaparece do dropdown mas histórico fica)

---

## Finalização do Kit (modal existente em `session.html`)

O modal de finalização já tem campos `veiculo` e `garagem` como texto livre. Mudanças:

1. Adicionar **dropdown pesquisável** de veículos filtrados pelo `cliente` do template atual
2. Opção "— sem veículo definido —" no topo (padrão)
3. Ao selecionar um veículo: preenche automaticamente `garagem` com o valor do cadastro (editável)
4. Campo de texto livre de garagem continua disponível (para kits sem veículo cadastrado)
5. O campo `veiculo` (texto) passa a ser preenchido automaticamente com `numero` do veículo selecionado

Backend: rota de finalização (`POST /session/{id}/finalizar`) recebe novo campo `veiculo_id` (opcional, int). Quando presente, grava em `kit_record.veiculo_id`.

---

## Vínculo Pós-Finalização

No relatório principal (`/reports`), cada kit terá um botão **"🔗 Vincular Veículo"** (ou mostra o número do veículo já vinculado). Ao clicar: modal simples com dropdown de veículos do mesmo cliente → `POST /kit-record/{kit_id}/veiculo`.

---

## Importação Excel

- Formato esperado: colunas `Número do Veículo` e `Cliente` (case-insensitive, ignora colunas extras)
- Linhas duplicadas (mesmo número + cliente) são ignoradas (não geram erro)
- Após import: página mostra resumo `X inseridos, Y ignorados`
- Modelo disponível para download com 2 linhas de exemplo

---

## Fora do Escopo

- Vinculação automática com base em texto livre de kits antigos
- Fotos ou documentos do veículo
- Alertas de veículo sem kit há N dias
- Permissões por cliente/empresa
