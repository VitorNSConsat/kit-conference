// Captura global de teclado para leitor de código de barras USB
// O leitor envia os caracteres do código + Enter ao final

let ws = null;
let buffer = "";
let _aguardandoIdentificacao = false;
let _codigoPendente = null;
let _aguardandoSerial = false;
let _aguardandoPatrimonioFixo = false;
let _codigoSubstituicaoPendente = null;
let _codigoQuantidadePendente = null;

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

        if (data.resultado === "aguardando_patrimonio_fixo") {
            mostrarBannerPatrimonioFixo(data);
            adicionarEvento(data);
            return;
        }
        if (data.resultado === "cancelado_patrimonio_fixo") {
            ocultarBannerPatrimonioFixo();
            adicionarEvento(data);
            return;
        }
        if (data.resultado === "aguardando_serial") {
            ocultarBannerPatrimonioFixo();
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
            ocultarBannerPatrimonioFixo();
        } else if (data.resultado === "componente_pendente") {
            mostrarModalComponente(data);
            return;
        } else if (data.resultado === "componente") {
            data.atualizacoes.forEach(u => {
                atualizarContagem(u.item_tipo_id, u.contagem_atual, u.quantidade_exigida);
            });
        } else if (data.resultado === "quantidade_pendente") {
            mostrarModalQuantidade(data);
        } else if (data.resultado === "substituicao_pendente") {
            mostrarModalSubstituicao(data);
        } else if (data.resultado === "desconhecido") {
            mostrarModalIdentificacao(data.codigo_barra, data.tipos);
        }
    };

    // Captura global: acumula chars até Enter (leitor USB físico, sem foco em nenhum input)
    document.addEventListener("keydown", (e) => {
        // Ignora bipagens enquanto o modal de identificação está aberto
        if (_aguardandoIdentificacao) return;

        // Ignora eventos originados de um <input> (ex.: #mobile-barcode-input).
        // Esses campos já processam seu próprio Enter (_enviarInputMobile());
        // sem este filtro, o keydown borbulha até aqui e o código é enviado
        // duas vezes — uma pelo input, outra por este buffer global.
        if (e.target && e.target.tagName === 'INPUT') return;

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

function _fmtQtd(n) {
    const f = Math.round(parseFloat(n) * 100) / 100;
    return Number.isInteger(f) ? f.toString() : f.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
}

function atualizarContagem(itemTipoId, atual, _exigido) {
    let scrollAlvo = null;
    document.querySelectorAll(`.item-row[data-tipo-id="${itemTipoId}"]`).forEach(el => {
        const exigido = parseFloat(el.dataset.exigido);
        const unidade = el.dataset.unidade || 'un';
        const sufixo = unidade === 'm' ? 'm' : '';
        el.querySelector(".count").textContent =
            `${_fmtQtd(atual)}${sufixo}/${_fmtQtd(exigido)}${sufixo}`;
        if (parseFloat(atual) >= exigido - 0.001) {
            el.classList.remove("pending");
            el.classList.add("done");
            el.querySelector(".check").textContent = "✅";
        }
        if (!scrollAlvo) scrollAlvo = el;
    });
    if (scrollAlvo) scrollAlvo.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const pendentes = document.querySelectorAll(".item-row.pending[data-obrigatorio='true']");
    document.getElementById("btn-finalizar").disabled = pendentes.length > 0;
}

// ── Banner de patrimônio fixo ─────────────────────────────────────────────────

function mostrarBannerPatrimonioFixo(data) {
    _aguardandoPatrimonioFixo = true;
    document.getElementById("banner-fixo-nome").textContent = data.tipo_nome;
    document.getElementById("banner-fixo").style.display = "flex";
}

function ocultarBannerPatrimonioFixo() {
    _aguardandoPatrimonioFixo = false;
    document.getElementById("banner-fixo").style.display = "none";
    document.getElementById("banner-fixo-nome").textContent = "";
}

function cancelarPatrimonioFixo() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({acao: "cancelar_patrimonio_fixo"}));
    }
    ocultarBannerPatrimonioFixo();
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

// ── Modal de quantidade (item com múltiplas unidades) ────────────────────────

function mostrarModalQuantidade(data) {
    _codigoQuantidadePendente = data.codigo_barra;
    _aguardandoIdentificacao = true;
    buffer = "";
    const isMeter = (data.unidade === 'm');
    const sufixo = isMeter ? 'm' : '';
    document.getElementById("qtd-titulo").textContent =
        isMeter ? "📏 Metragem" : "📦 Quantidade de Unidades";
    document.getElementById("qtd-label").textContent =
        isMeter ? "Quantos metros você está adicionando?" : "Quantas unidades você está adicionando?";
    document.getElementById("qtd-mensagem").innerHTML =
        `<strong>${data.descricao}</strong> — ` +
        `<code style="background:#f0f0f0;padding:2px 6px;border-radius:4px;">${data.codigo_barra}</code>`;
    document.getElementById("qtd-info").textContent =
        `Kit exige ${_fmtQtd(data.exigido)}${sufixo} · ${_fmtQtd(data.atual)}${sufixo} já adicionados · restam ${_fmtQtd(data.restante)}${sufixo}`;
    document.getElementById("qtd-sufixo").textContent = sufixo;
    const input = document.getElementById("qtd-valor");
    input.max = data.restante;
    input.value = _fmtQtd(data.restante);
    input.step = isMeter ? "0.01" : "1";
    input.min = isMeter ? "0.01" : "1";
    document.getElementById("modal-quantidade").style.display = "flex";
    input.select();
    input.focus();
}

function confirmarQuantidade() {
    const qtd = parseFloat(document.getElementById("qtd-valor").value) || 0;
    if (qtd <= 0) { document.getElementById("qtd-valor").focus(); return; }
    if (ws && ws.readyState === WebSocket.OPEN && _codigoQuantidadePendente) {
        ws.send(JSON.stringify({
            acao: "confirmar_quantidade",
            codigo_barra: _codigoQuantidadePendente,
            quantidade: qtd
        }));
    }
    fecharModalQuantidade();
}

function fecharModalQuantidade() {
    document.getElementById("modal-quantidade").style.display = "none";
    _codigoQuantidadePendente = null;
    _aguardandoIdentificacao = false;
    buffer = "";
    document.getElementById("scan-buffer").textContent = "";
}

// ── Modal de substituição de patrimônio ──────────────────────────────────────

function mostrarModalSubstituicao(data) {
    _codigoSubstituicaoPendente = data.codigo_barra;
    _aguardandoIdentificacao = true;
    buffer = "";
    document.getElementById("subs-mensagem").innerHTML =
        `Patrimônio <code style="background:#f0f0f0;padding:2px 6px;border-radius:4px;">${data.codigo_barra}</code> ` +
        `foi utilizado no kit <strong>${data.kit_id}</strong>.<br>` +
        `Informe o motivo da substituição para continuar.`;
    document.getElementById("subs-motivo").value = "";
    document.getElementById("modal-substituicao").style.display = "flex";
    document.getElementById("subs-motivo").focus();
}

function confirmarSubstituicao() {
    const motivo = document.getElementById("subs-motivo").value.trim();
    if (!motivo) {
        document.getElementById("subs-motivo").focus();
        return;
    }
    if (ws && ws.readyState === WebSocket.OPEN && _codigoSubstituicaoPendente) {
        ws.send(JSON.stringify({
            acao: "confirmar_substituicao",
            codigo_barra: _codigoSubstituicaoPendente,
            motivo: motivo
        }));
    }
    fecharModalSubstituicao();
}

function fecharModalSubstituicao() {
    document.getElementById("modal-substituicao").style.display = "none";
    _codigoSubstituicaoPendente = null;
    _aguardandoIdentificacao = false;
    buffer = "";
    document.getElementById("scan-buffer").textContent = "";
}
