# Design: Cadastro de Clientes

**Data:** 2026-07-17
**Status:** Aprovado

## Contexto

Atualmente "cliente" é um campo de texto livre em `kit_template` e `veiculos`. Isso permitia erros de digitação que quebravam o filtro do dropdown de veículos na tela de beep. O objetivo é criar uma tabela `clientes` como fonte de verdade, e fazer todos os campos de cliente (templates e veículos) usarem um `<select>` estrito a partir dela.

---

## Banco de Dados

Nova tabela `clientes`:

```sql
CREATE TABLE IF NOT EXISTS clientes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    nome      TEXT NOT NULL UNIQUE,
    criado_em TEXT NOT NULL
);
```

Sem alterar `kit_template.cliente` ou `veiculos.cliente` — continuam como TEXT (retrocompatibilidade). O select nos formulários garante consistência a partir de agora.

---

## Módulo `app/clientes.py`

- `listar() -> list[dict]` — retorna todos os clientes ordenados por nome (`id, nome, criado_em`)
- `criar(nome: str) -> int | None` — insere novo cliente; retorna `None` se já existir (UNIQUE)

---

## Rotas (todas em `main.py`)

| Método | URL | Descrição |
|--------|-----|-----------|
| GET | `/admin/veiculos` | já existe — recebe `clientes` no contexto |
| POST | `/admin/clientes` | cria novo cliente; redireciona para `/admin/veiculos` |

Não há página separada para clientes — o CRUD é inline na página de Veículos e Clientes.

---

## Tela `/admin/veiculos` (renomeada "Veículos e Clientes")

Adicionar no topo da página, antes da seção de veículos:

**Seção "Clientes":**
- Tabela: Nome · Data de cadastro
- Formulário inline: campo "Nome do Cliente" + botão "＋ Adicionar"
- Mensagem de erro se nome já existir

O campo "Cliente" no formulário de novo veículo e edição de veículo passa a ser `<select>` com os clientes desta tabela.

---

## Telas de Kit (`/admin/templates` e edição)

Campo "Cliente" muda de `<input type="text">` para `<select>` populado de `clientes.listar()`. As rotas de templates recebem `clientes` no contexto.

---

## Navbar e títulos

- `templates/base.html`: link `🚗 Veículos` → `🚗 Veículos e Clientes`
- `templates/admin_veiculos.html`: título `<h1>Veículos</h1>` → `<h1>Veículos e Clientes</h1>`

---

## Fora do escopo

- Edição ou exclusão de clientes (risco de inconsistência com registros existentes)
- Migração automática de clientes existentes em `kit_template`/`veiculos` para a nova tabela
- Permissões por cliente
