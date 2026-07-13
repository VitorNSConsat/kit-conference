// Captura global de teclado para leitor de código de barras USB
// O leitor envia os caracteres do código + Enter ao final

let ws = null;
let buffer = "";
let _aguardandoIdentificacao = false;
let _codigoPendente = null;

function initScanner(sessaoId) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/session/${sessaoId}`);

    ws.onopen = () => {
        document.getElementById("ws-status").textContent = "🟢 Conectado";
    };

    ws.onclose = () => {
        document.getElementById("ws-status").textContent = "🔴 Desconectado — recarregue a página";
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        adicionarEvento(data);
        if (data.resultado === "aceito") {
            atualizarContagem(data.item_tipo_id, data.contagem_atual, data.quantidade_exigida);
        } else if (data.resultado === "componente") {
            data.atualizacoes.forEach(u => {
                atualizarContagem(u.item_tipo_id, u.contagem_atual, u.quantidade_exigida);
            });
        } else if (data.resultado === "desconhecido") {
            mostrarModalIdentificacao(data.codigo_barra, data.tipos);
        }
    };

    // Captura global: acumula chars até Enter
    document.addEventListener("keydown", (e) => {
        // Ignora bipagens enquanto o modal de identificação está aberto
        if (_aguardandoIdentificacao) return;

        if (e.key === "Enter") {
            const codigo = buffer.trim();
            buffer = "";
            document.getElementById("scan-buffer").textContent = "";
            if (codigo && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(codigo);
            }
        } else if (e.key.length === 1) {
            buffer += e.key;
            document.getElementById("scan-buffer").textContent = "Lendo: " + buffer;
        }
    });
}

function adicionarEvento(data) {
    const feed = document.getElementById("scan-feed");
    const div = document.createElement("div");
    div.className = `scan-event ${data.resultado}`;
    const hora = new Date().toLocaleTimeString("pt-BR");
    div.innerHTML = `<strong>${hora}</strong> — ${data.mensagem}`;
    feed.prepend(div);
    while (feed.children.length > 50) feed.removeChild(feed.lastChild);
}

function atualizarContagem(itemTipoId, atual, exigido) {
    const el = document.getElementById(`item-tipo-${itemTipoId}`);
    if (!el) return;
    el.querySelector(".count").textContent = `${atual}/${exigido}`;
    if (atual >= exigido) {
        el.classList.remove("pending");
        el.classList.add("done");
        el.querySelector(".check").textContent = "✅";
    }
    const pendentes = document.querySelectorAll(".item-row.pending[data-obrigatorio='true']");
    document.getElementById("btn-finalizar").disabled = pendentes.length > 0;
}

// ── Modal de identificação — grade de cards clicáveis ────────────────────────

function mostrarModalIdentificacao(codigoBarra, tipos) {
    _codigoPendente = codigoBarra;
    _aguardandoIdentificacao = true;
    buffer = "";

    const modal = document.getElementById("modal-identificar");
    const grid = document.getElementById("modal-tipos-grid");
    const msg = document.getElementById("modal-mensagem");

    msg.innerHTML = `Patrimônio <code style="background:#f0f0f0;padding:2px 6px;border-radius:4px;">${codigoBarra}</code> não está cadastrado. Selecione o tipo do item:`;
    grid.innerHTML = "";

    if (tipos.length === 0) {
        grid.innerHTML = '<p style="color:#888;grid-column:1/-1;font-size:13px;">Nenhum tipo disponível para este kit. Configure os tipos em Admin → Itens.</p>';
    } else {
        tipos.forEach(t => {
            const btn = document.createElement("button");
            btn.className = "tipo-card";
            btn.textContent = t.nome;
            btn.onclick = () => confirmarTipo(t.id);
            grid.appendChild(btn);
        });
    }

    modal.style.display = "flex";
}

function confirmarTipo(tipoId) {
    if (ws && ws.readyState === WebSocket.OPEN && _codigoPendente) {
        ws.send(JSON.stringify({
            acao: "identificar",
            codigo: _codigoPendente,
            item_tipo_id: tipoId
        }));
    }
    fecharModal();
}

function fecharModal() {
    document.getElementById("modal-identificar").style.display = "none";
    _codigoPendente = null;
    _aguardandoIdentificacao = false;
    buffer = "";
    document.getElementById("scan-buffer").textContent = "";
}
