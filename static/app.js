// Captura global de teclado para leitor de código de barras USB
// O leitor envia os caracteres do código + Enter ao final

let ws = null;
let buffer = "";

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
            atualizarContagem(data.codigo_barra, data.contagem_atual, data.quantidade_exigida);
        }
    };

    // Captura global: acumula chars até Enter
    document.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            const codigo = buffer.trim();
            buffer = "";
            document.getElementById("scan-buffer").textContent = "";
            if (codigo && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(codigo);
            }
        } else if (e.key.length === 1) {
            // só caracteres imprimíveis
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
    // manter no máximo 50 eventos
    while (feed.children.length > 50) feed.removeChild(feed.lastChild);
}

function atualizarContagem(codigoBarra, atual, exigido) {
    const el = document.getElementById(`item-${CSS.escape(codigoBarra)}`);
    if (!el) return;
    el.querySelector(".count").textContent = `${atual}/${exigido}`;
    if (atual >= exigido) {
        el.classList.remove("pending");
        el.classList.add("done");
        el.querySelector(".check").textContent = "✅";
    }
    // Verifica se todos os itens obrigatórios estão completos
    const pendentes = document.querySelectorAll(".item-row.pending[data-obrigatorio='true']");
    document.getElementById("btn-finalizar").disabled = pendentes.length > 0;
}
