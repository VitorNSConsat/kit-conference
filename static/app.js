// Captura global de teclado para leitor de código de barras USB
// O leitor envia os caracteres do código + Enter ao final

let ws = null;
let buffer = "";
let _aguardandoIdentificacao = false;
let _codigoPendente = null;
let _aguardandoSerial = false;

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

        if (data.resultado === "aguardando_serial") {
            mostrarBannerSerial(data);
            adicionarEvento(data);
            return;
        }
        if (data.resultado === "cancelado_serial") {
            ocultarBannerSerial();
            adicionarEvento(data);
            return;
        }

        adicionarEvento(data);

        if (data.resultado === "aceito") {
            atualizarContagem(data.item_tipo_id, data.contagem_atual, data.quantidade_exigida);
            if (data.serial_number) ocultarBannerSerial();
        } else if (data.resultado === "componente_pendente") {
            mostrarModalComponente(data);
            return;
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

function atualizarContagem(itemTipoId, atual, _exigido) {
    // Atualiza todas as linhas com este tipo (pode aparecer mais de uma vez no template)
    document.querySelectorAll(`.item-row[data-tipo-id="${itemTipoId}"]`).forEach(el => {
        const exigido = parseInt(el.dataset.exigido);
        el.querySelector(".count").textContent = `${atual}/${exigido}`;
        if (atual >= exigido) {
            el.classList.remove("pending");
            el.classList.add("done");
            el.querySelector(".check").textContent = "✅";
        }
    });
    const pendentes = document.querySelectorAll(".item-row.pending[data-obrigatorio='true']");
    document.getElementById("btn-finalizar").disabled = pendentes.length > 0;
}

// ── Banner de serial number ───────────────────────────────────────────────────

function mostrarBannerSerial(data) {
    _aguardandoSerial = true;
    document.getElementById("banner-serial-desc").textContent = data.descricao;
    document.getElementById("banner-serial-codigo").textContent = `(${data.codigo_barra})`;
    document.getElementById("banner-serial").style.display = "flex";
}

function ocultarBannerSerial() {
    _aguardandoSerial = false;
    document.getElementById("banner-serial").style.display = "none";
    document.getElementById("banner-serial-desc").textContent = "";
    document.getElementById("banner-serial-codigo").textContent = "";
}

function cancelarSerial() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({acao: "cancelar_serial"}));
    }
    ocultarBannerSerial();
}

// ── Modal de componente — confirmação antes de registrar ─────────────────────

let _codigoComponentePendente = null;

function mostrarModalComponente(data) {
    _codigoComponentePendente = data.codigo_barra;
    _aguardandoIdentificacao = true;
    buffer = "";

    document.getElementById("comp-codigo").textContent = "Código: " + data.codigo_barra;

    const lista = document.getElementById("comp-itens-lista");
    lista.innerHTML = "";
    data.itens.forEach(item => {
        const completo = item.faltam === 0;
        const div = document.createElement("div");
        div.style.cssText = "display:grid;grid-template-columns:1fr auto auto;align-items:center;" +
            "gap:10px;padding:10px 14px;background:" + (completo ? "#f0fff4" : "#f4f7fb") +
            ";border-radius:8px;border:1px solid " + (completo ? "#a8e6c0" : "#e0e7ef") + ";";
        div.innerHTML =
            `<div>
                <div style="font-weight:600;font-size:14px;">${item.descricao}</div>
                <div style="font-size:11px;color:#888;margin-top:2px;">${item.atual}/${item.quantidade_exigida} já bipados</div>
             </div>` +
            (completo
                ? `<span style="color:#27ae60;font-size:18px;">✅</span>
                   <input type="hidden" data-tipo-id="${item.item_tipo_id}" value="0">`
                : `<label style="font-size:12px;color:#555;">Qtd:</label>
                   <input type="number" data-tipo-id="${item.item_tipo_id}"
                          value="${item.faltam}" min="0" max="${item.faltam}"
                          style="width:64px;text-align:center;font-size:15px;font-weight:700;
                                 padding:4px 6px;border:2px solid #1a3a5c;border-radius:6px;">`);
        lista.appendChild(div);
    });

    document.getElementById("modal-componente").style.display = "flex";
    const primeiro = lista.querySelector("input[type=number]");
    if (primeiro) primeiro.focus();
}

function confirmarComponente() {
    if (ws && ws.readyState === WebSocket.OPEN && _codigoComponentePendente) {
        const quantidades = {};
        document.querySelectorAll("#comp-itens-lista input[data-tipo-id]").forEach(inp => {
            quantidades[inp.dataset.tipoId] = parseInt(inp.value) || 0;
        });
        ws.send(JSON.stringify({
            acao: "confirmar_componente",
            codigo_barra: _codigoComponentePendente,
            quantidades: quantidades
        }));
    }
    fecharModalComponente();
}

function cancelarComponente() {
    fecharModalComponente();
}

function fecharModalComponente() {
    document.getElementById("modal-componente").style.display = "none";
    _codigoComponentePendente = null;
    _aguardandoIdentificacao = false;
    buffer = "";
    document.getElementById("scan-buffer").textContent = "";
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
