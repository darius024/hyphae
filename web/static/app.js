const messagesEl = document.getElementById("messages");
const queryInput = document.getElementById("query-input");
const sendBtn = document.getElementById("send-btn");
const voiceBtn = document.getElementById("voice-btn");
const docListEl = document.getElementById("doc-list-items");
const toolListEl = document.getElementById("tool-list");
const quickButtonsEl = document.getElementById("quick-buttons");
const fileInput = document.getElementById("file-input");
const uploadBtn = document.getElementById("upload-btn");

let isRecording = false;
let mediaRecorder = null;
let isBusy = false;

const HISTORY_KEY = "hyphae_chat_history";

function saveHistory() {
    try {
        const msgs = [];
        messagesEl.querySelectorAll(".message").forEach(el => {
            if (el.id === "thinking" || el.classList.contains("tool-details")) return;
            const role = el.classList.contains("user") ? "user" : "assistant";
            const bubble = el.querySelector(".bubble");
            if (!bubble) return;
            const meta = {};
            const routeBadge = el.querySelector(".badge.local, .badge.cloud");
            const confBadge = el.querySelector(".badge.conf-high, .badge.conf-med, .badge.conf-low");
            const metaSpan = el.querySelector(".meta span:last-child");
            if (routeBadge) meta.source = routeBadge.classList.contains("local") ? "on-device" : "cloud";
            if (metaSpan) meta.routing_ms = metaSpan.textContent.replace("ms", "");
            if (confBadge) {
                if (confBadge.classList.contains("conf-high")) meta.confidence = 1;
                else if (confBadge.classList.contains("conf-med")) meta.confidence = 0.5;
                else meta.confidence = 0.1;
            }
            msgs.push({ role, text: bubble.textContent, html: bubble.innerHTML, meta: Object.keys(meta).length ? meta : null });
        });
        localStorage.setItem(HISTORY_KEY, JSON.stringify(msgs));
    } catch {}
}

function loadHistory() {
    try {
        const raw = localStorage.getItem(HISTORY_KEY);
        if (!raw) return;
        const msgs = JSON.parse(raw);
        if (!msgs.length) return;
        messagesEl.innerHTML = "";
        for (const m of msgs) {
            const div = document.createElement("div");
            div.className = `message ${m.role}`;
            let html = `<div class="bubble">${m.html || escapeHtml(m.text)}</div>`;
            html += buildMetaHtml(m.meta);
            div.innerHTML = html;
            messagesEl.appendChild(div);
        }
        scrollToBottom();
    } catch {}
}

function clearHistory() {
    localStorage.removeItem(HISTORY_KEY);
    messagesEl.innerHTML = `<div class="message assistant">
        <div class="bubble">
            <strong>Welcome to Hyphae.</strong> Ask questions about your research documents,
            search literature, generate hypotheses, or manage your corpus.
            <span class="privacy-note">Your confidential data stays on-device.</span>
        </div>
    </div>`;
}

function setBusy(busy) {
    isBusy = busy;
    sendBtn.disabled = busy;
    voiceBtn.disabled = busy && !isRecording;
    queryInput.disabled = busy;
    if (busy) {
        sendBtn.classList.add("disabled");
    } else {
        sendBtn.classList.remove("disabled");
        queryInput.focus();
    }
}

// ── Messages ────────────────────────────────────────────────────────

function renderMarkdown(text) {
    let html = escapeHtml(text);
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    html = html.replace(/^[-•]\s+(.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>.*<\/li>)/gs, "<ul>$1</ul>");
    html = html.replace(/<\/ul>\s*<ul>/g, "");
    html = html.replace(/^\d+\.\s+(.+)$/gm, "<li>$1</li>");
    html = html.replace(/\n/g, "<br>");
    return html;
}

function confidenceBadge(value) {
    if (value == null) return "";
    const n = parseFloat(value);
    if (isNaN(n)) return "";
    if (n >= 0.8) return '<span class="badge conf-high">HIGH</span>';
    if (n >= 0.4) return '<span class="badge conf-med">MED</span>';
    return '<span class="badge conf-low">LOW</span>';
}

function buildMetaHtml(meta) {
    if (!meta) return "";
    const isLocal = meta.source && meta.source.includes("on-device");
    const routeBadge = isLocal
        ? '<span class="badge local">LOCAL</span>'
        : '<span class="badge cloud">CLOUD</span>';
    const confBadge = confidenceBadge(meta.confidence);
    return `<div class="meta">${routeBadge}${confBadge} <span>${meta.routing_ms}ms</span></div>`;
}

function addMessage(role, content, meta) {
    const div = document.createElement("div");
    div.className = `message ${role}`;

    const rendered = role === "assistant" ? renderMarkdown(content) : escapeHtml(content);
    let html = `<div class="bubble">${rendered}</div>`;
    html += buildMetaHtml(meta);

    div.innerHTML = html;
    messagesEl.appendChild(div);
    scrollToBottom();
    saveHistory();
    return div;
}

function addErrorMessage(text, retryFn) {
    const div = document.createElement("div");
    div.className = "message assistant";

    let html = `<div class="bubble error-bubble">${escapeHtml(text)}`;
    if (retryFn) {
        html += ` <button class="retry-btn">Retry</button>`;
    }
    html += `</div>`;

    div.innerHTML = html;
    if (retryFn) {
        div.querySelector(".retry-btn").addEventListener("click", () => {
            div.remove();
            retryFn();
        });
    }
    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
}

function formatToolResult(tool, result) {
    if (result.error) return `<span class="tool-error">Error: ${escapeHtml(result.error)}</span>`;

    switch (tool) {
        case "search_papers": {
            const items = (result.results || []).slice(0, 5);
            if (!items.length) return "No matching passages found.";
            return `<div class="search-results">${items.map((r, i) => {
                const score = r.score != null ? `<span class="score">${r.score.toFixed(2)}</span>` : "";
                const text = escapeHtml((r.text || "").slice(0, 240).replace(/\n/g, " "));
                const doc = escapeHtml(r.source || r.path || r.name || "");
                const cite = doc ? `<span class="doc">[${doc}]</span>` : "";
                return `<div class="search-item">${score}<span>${text} ${cite}</span></div>`;
            }).join("")}</div>`;
        }
        case "summarise_notes":
            return renderMarkdown(result.summary || "No summary available.");
        case "create_note":
            return `Note saved to <code>${escapeHtml(result.saved || "")}</code>`;
        case "list_documents": {
            const docs = result.documents || [];
            if (!docs.length) return "Corpus is empty.";
            return `<div class="doc-table">${docs.map(d =>
                `<div class="doc-row"><span>${escapeHtml(d.name)}</span><span class="doc-size">${d.size_kb} KB</span></div>`
            ).join("")}</div>`;
        }
        case "generate_hypothesis":
            return renderMarkdown(result.hypotheses || "");
        case "search_literature":
            return renderMarkdown(result.results || "");
        case "search_text": {
            const items = result.matches || [];
            if (!items.length) return "No matches found.";
            return `<div class="search-results">${items.map(m => {
                const para = escapeHtml((m.paragraph || m.snippet || "").slice(0, 320));
                const doc = escapeHtml(m.name || "");
                return `<div class="search-item"><span class="doc">[${doc}]</span><span>${para}</span></div>`;
            }).join("")}</div>`;
        }
        case "compare_documents":
            return renderMarkdown(result.comparison || "");
        default:
            return `<pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre>`;
    }
}

function addToolResults(functionCalls, toolResults) {
    if (!toolResults || toolResults.length === 0) return;

    const div = document.createElement("div");
    div.className = "message assistant tool-details";

    const summary = document.createElement("div");
    summary.className = "tool-toggle";
    summary.innerHTML = `<span class="toggle-icon">&#9654;</span> <span class="toggle-label">${toolResults.length} tool call${toolResults.length > 1 ? "s" : ""}</span>`;
    const content = document.createElement("div");
    content.className = "tool-content collapsed";

    let html = "";
    for (const tr of toolResults) {
        const argsStr = Object.entries(tr.arguments || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ");
        html += `<div class="tool-call">
            <span class="tool-name">${escapeHtml(tr.tool)}</span><span class="tool-args-inline">(${escapeHtml(argsStr)})</span>
        </div>`;
        html += `<div class="tool-result">${formatToolResult(tr.tool, tr.result)}</div>`;
    }
    content.innerHTML = html;

    summary.addEventListener("click", () => {
        const collapsed = content.classList.toggle("collapsed");
        summary.querySelector(".toggle-icon").innerHTML = collapsed ? "&#9654;" : "&#9660;";
    });

    div.appendChild(summary);
    div.appendChild(content);
    messagesEl.appendChild(div);
    scrollToBottom();
}

function addThinking() {
    const div = document.createElement("div");
    div.className = "message assistant";
    div.id = "thinking";
    div.innerHTML = '<div class="thinking"><span></span><span></span><span></span></div>';
    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
}

function removeThinking() {
    const el = document.getElementById("thinking");
    if (el) el.remove();
}

function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// ── Query ───────────────────────────────────────────────────────────

async function sendQuery(text) {
    if (!text.trim() || isBusy) return;

    addMessage("user", text);
    queryInput.value = "";
    queryInput.style.height = "auto";

    setBusy(true);
    addThinking();

    try {
        const res = await fetch("/api/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text }),
        });
        const data = await res.json();
        removeThinking();

        if (!res.ok || data.error || data.detail) {
            const errMsg = data.error || data.detail || `Server error (${res.status})`;
            addErrorMessage(`Error: ${errMsg}`, () => sendQuery(text));
            return;
        }

        const meta = { source: data.source, routing_ms: data.routing_ms, confidence: data.confidence };
        const calls = data.function_calls || [];

        if (data.answer) {
            addMessage("assistant", data.answer, meta);
        } else if (calls.length > 0) {
            addMessage("assistant", `Called ${calls.map(fc => fc.name).join(", ")}`, meta);
        } else {
            addMessage("assistant", "I couldn't find a relevant tool for that query. Try rephrasing?", meta);
        }

        addToolResults(calls, data.tool_results);
    } catch (err) {
        removeThinking();
        addErrorMessage(`Network error: ${err.message}`, () => sendQuery(text));
    } finally {
        setBusy(false);
    }
}

// ── Voice ───────────────────────────────────────────────────────────

async function toggleVoice() {
    if (isRecording) {
        stopRecording();
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
            ? "audio/webm;codecs=opus"
            : MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "";
        mediaRecorder = mimeType
            ? new MediaRecorder(stream, { mimeType })
            : new MediaRecorder(stream);
        const chunks = [];

        mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(t => t.stop());
            const actualMime = mediaRecorder.mimeType || "audio/webm";
            const ext = actualMime.includes("mp4") ? ".mp4" : ".webm";
            const blob = new Blob(chunks, { type: actualMime });
            await sendVoice(blob, ext);
        };

        mediaRecorder.start();
        isRecording = true;
        voiceBtn.classList.add("recording");
    } catch (err) {
        addErrorMessage(`Microphone access denied: ${err.message}`);
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
    isRecording = false;
    voiceBtn.classList.remove("recording");
}

async function sendVoice(blob, ext = ".webm") {
    setBusy(true);
    addThinking();
    const form = new FormData();
    form.append("audio", blob, `recording${ext}`);

    try {
        const res = await fetch("/api/voice", { method: "POST", body: form });
        const data = await res.json();
        removeThinking();

        if (!res.ok || data.error || data.detail) {
            const errMsg = data.error || data.detail || `Server error (${res.status})`;
            const hint = data.hint ? `\n${data.hint}` : "";
            addErrorMessage(`Voice error: ${errMsg}${hint}`);
            return;
        }

        addMessage("user", `🎤 "${data.transcript}"`);
        const meta = { source: data.source, routing_ms: data.routing_ms, confidence: data.confidence };
        const calls = data.function_calls || [];

        if (data.answer) {
            addMessage("assistant", data.answer, meta);
        } else if (calls.length > 0) {
            addMessage("assistant", `Called ${calls.map(fc => fc.name).join(", ")}`, meta);
        } else {
            addMessage("assistant", "I couldn't process that. Try again?", meta);
        }

        addToolResults(calls, data.tool_results);
    } catch (err) {
        removeThinking();
        addErrorMessage(`Voice error: ${err.message}`);
    } finally {
        setBusy(false);
    }
}

// ── Documents ───────────────────────────────────────────────────────

let allDocuments = [];
const docSearchInput = document.getElementById("doc-search");

docSearchInput.addEventListener("input", () => {
    const q = docSearchInput.value.toLowerCase();
    const filtered = q ? allDocuments.filter(d => d.name.toLowerCase().includes(q)) : allDocuments;
    renderDocuments(filtered);
});

async function loadDocuments() {
    docListEl.innerHTML = '<div class="doc-skeleton"><div></div><div></div><div></div></div>';
    try {
        const res = await fetch("/api/documents");
        const data = await res.json();
        allDocuments = data.documents || [];
        const q = docSearchInput.value.toLowerCase();
        const filtered = q ? allDocuments.filter(d => d.name.toLowerCase().includes(q)) : allDocuments;
        renderDocuments(filtered);
    } catch {
        docListEl.innerHTML = '<div class="doc-item"><span class="name" style="color:var(--red)">Failed to load</span></div>';
    }
}

function renderDocuments(docs) {
    if (!docs || docs.length === 0) {
        docListEl.innerHTML = '<div class="doc-empty">No documents yet. Upload PDFs or text files to get started.</div>';
        return;
    }

    docListEl.innerHTML = docs.map(d => `
        <div class="doc-item">
            <span class="name doc-preview-link" data-doc="${escapeHtml(d.name)}">${escapeHtml(d.name)}</span>
            <span class="size">${d.size_kb} KB</span>
            <button class="remove-btn" onclick="removeDoc('${escapeHtml(d.name)}')" title="Remove">×</button>
        </div>
    `).join("");

    docListEl.querySelectorAll(".doc-preview-link").forEach(el => {
        el.addEventListener("click", () => previewDoc(el.dataset.doc));
    });
}

// ── Tools list (UI helper) ─────────────────────────────────────────

function toolSourceBadge(source) {
    if (source === "local") return '<span class="badge local">LOCAL</span>';
    if (source === "cloud") return '<span class="badge cloud">CLOUD</span>';
    return '<span class="badge hybrid">HYBRID</span>';
}

async function loadTools() {
    if (!toolListEl) return;
    toolListEl.innerHTML = '<div class="doc-skeleton"><div></div><div></div><div></div></div>';
    try {
        const res = await fetch("/api/tools");
        const data = await res.json();
        renderTools(data.tools || []);
    } catch (err) {
        toolListEl.innerHTML = `<div class="doc-empty">Failed to load tools (${escapeHtml(err.message)})</div>`;
    }
}

function renderTools(tools) {
    if (!tools || tools.length === 0) {
        toolListEl.innerHTML = '<div class="doc-empty">No tools available.</div>';
        return;
    }

    toolListEl.innerHTML = tools.map(t => {
        const params = (t.parameters || []).map(p => {
            const req = p.required ? '<span style="color:var(--red)">*</span>' : '';
            return `<div class="tool-param"><strong>${escapeHtml(p.name)}</strong>${req} <span class="param-type">(${escapeHtml(p.type || "string")})</span> — ${escapeHtml(p.description || "")}</div>`;
        }).join("") || "<div class=\"tool-param\">No parameters</div>";
        const requiredNames = (t.parameters || []).filter(p => p.required).map(p => p.name);
        const usage = requiredNames.length ? `${t.name}(${requiredNames.map(n => `${n}: …`).join(", ")})` : `${t.name}()`;
        return `
            <div class="tool-item">
                <div class="tool-name">${escapeHtml(t.name || "tool")}${toolSourceBadge(t.source)}</div>
                <div class="tool-desc">${escapeHtml(t.description || "")}</div>
                <div class="tool-params">${params}</div>
                <div class="tool-usage">Example call: <code>${escapeHtml(usage)}</code></div>
            </div>
        `;
    }).join("");
}

// ── Quick research prompts ─────────────────────────────────────────

function loadQuickPrompts() {
    if (!quickButtonsEl) return;
    const prompts = [
        {
            title: "📑 Summary w/ citations",
            hint: "Scan corpus and cite source filenames",
            text: "Summarize the corpus notes with inline citations [filename] and highlight gaps to investigate next."
        },
        {
            title: "🔬 Compare documents",
            hint: "Contrast two files on a topic",
            text: "Compare polymer_synthesis_notes.txt vs battery_cycling_log.txt on conductive additives; output key differences with citations."
        },
        {
            title: "🧪 Design experiment",
            hint: "Propose next steps & metrics",
            text: "Propose the next experiment to improve conductivity >1 S/cm while keeping self-healing >85%; include materials, protocol steps, and measurement plan with citations."
        },
        {
            title: "🌐 Literature + local",
            hint: "Blend local corpus with web",
            text: "Search literature on PEDOT:PSS self-healing hydrogels and combine with local corpus findings; cite online papers as [L1], [L2] and local files by name."
        }
    ];

    quickButtonsEl.innerHTML = prompts.map(p => `
        <button class="quick-btn" data-text="${escapeHtml(p.text)}">
            <strong>${escapeHtml(p.title)}</strong>
            <span>${escapeHtml(p.hint)}</span>
        </button>
    `).join("");

    quickButtonsEl.querySelectorAll(".quick-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            queryInput.value = btn.dataset.text;
            queryInput.focus();
            queryInput.dispatchEvent(new Event("input"));
        });
    });
}

async function removeDoc(name) {
    try {
        await fetch(`/api/documents/${encodeURIComponent(name)}`, { method: "DELETE" });
        loadDocuments();
    } catch {}
}

async function uploadFiles(files) {
    const form = new FormData();
    for (const f of files) form.append("file", f);

    uploadBtn.classList.add("uploading");
    uploadBtn.textContent = "Uploading...";

    try {
        const res = await fetch("/api/upload", { method: "POST", body: form });
        const data = await res.json();
        loadDocuments();
        const count = data.uploaded.filter(u => u.added).length;
        if (count > 0) {
            addMessage("assistant", `Uploaded ${count} document(s) to corpus.`);
        }
    } catch (err) {
        addErrorMessage(`Upload failed: ${err.message}`);
    } finally {
        uploadBtn.classList.remove("uploading");
        uploadBtn.textContent = "Drop files here or click to upload";
    }
}

// ── Event listeners ─────────────────────────────────────────────────

sendBtn.addEventListener("click", () => sendQuery(queryInput.value));

queryInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendQuery(queryInput.value);
    }
});

// ── Keyboard shortcuts ──────────────────────────────────────────────

document.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;

    // Cmd/Ctrl+K → focus doc search
    if (mod && e.key === "k") {
        e.preventDefault();
        docSearchInput.focus();
        if (window.innerWidth <= 768 && !sidebar.classList.contains("open")) {
            toggleSidebar();
        }
        return;
    }

    // Escape → close preview modal, clear doc search, or blur input
    if (e.key === "Escape") {
        if (previewOverlay.classList.contains("open")) {
            closePreview();
        } else if (document.activeElement === docSearchInput) {
            docSearchInput.value = "";
            docSearchInput.dispatchEvent(new Event("input"));
            docSearchInput.blur();
        } else if (document.activeElement === queryInput) {
            queryInput.blur();
        }
        return;
    }

    // Cmd/Ctrl+Enter → send query (works even with Shift held)
    if (mod && e.key === "Enter") {
        e.preventDefault();
        sendQuery(queryInput.value);
        return;
    }

    // "/" → focus query input (when not already in an input)
    if (e.key === "/" && !mod && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
        e.preventDefault();
        queryInput.focus();
    }
});

queryInput.addEventListener("input", () => {
    queryInput.style.height = "auto";
    queryInput.style.height = Math.min(queryInput.scrollHeight, 120) + "px";
});

voiceBtn.addEventListener("click", toggleVoice);

uploadBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
    if (fileInput.files.length > 0) uploadFiles(fileInput.files);
    fileInput.value = "";
});

// Drag and drop
uploadBtn.addEventListener("dragover", (e) => {
    e.preventDefault();
    uploadBtn.classList.add("dragover");
});
uploadBtn.addEventListener("dragleave", () => uploadBtn.classList.remove("dragover"));
uploadBtn.addEventListener("drop", (e) => {
    e.preventDefault();
    uploadBtn.classList.remove("dragover");
    if (e.dataTransfer.files.length > 0) uploadFiles(e.dataTransfer.files);
});

document.getElementById("clear-btn").addEventListener("click", clearHistory);

// ── Document preview modal ──────────────────────────────────────────

const previewOverlay = document.getElementById("preview-overlay");
const previewTitle = document.getElementById("preview-title");
const previewBody = document.getElementById("preview-body");

async function previewDoc(name) {
    previewTitle.textContent = name;
    previewBody.textContent = "Loading...";
    previewOverlay.classList.add("open");

    try {
        const res = await fetch(`/api/documents/${encodeURIComponent(name)}`);
        const data = await res.json();
        if (data.error) {
            previewBody.textContent = `Error: ${data.error}`;
        } else {
            previewBody.textContent = data.preview;
            previewTitle.textContent = `${name} (${data.size_kb} KB)`;
        }
    } catch (err) {
        previewBody.textContent = `Failed to load: ${err.message}`;
    }
}

function closePreview() { previewOverlay.classList.remove("open"); }

document.getElementById("preview-close").addEventListener("click", closePreview);
previewOverlay.addEventListener("click", (e) => {
    if (e.target === previewOverlay) closePreview();
});

// ── Mobile sidebar ──────────────────────────────────────────────────

const menuBtn = document.getElementById("menu-btn");
const sidebar = document.querySelector(".sidebar");
const sidebarOverlay = document.getElementById("sidebar-overlay");

function toggleSidebar() {
    sidebar.classList.toggle("open");
    sidebarOverlay.classList.toggle("open");
}

menuBtn.addEventListener("click", toggleSidebar);
sidebarOverlay.addEventListener("click", toggleSidebar);

// ── Init ────────────────────────────────────────────────────────────
loadHistory();
loadDocuments();
loadTools();
loadNotebooks();
loadQuickPrompts();

// ════════════════════════════════════════════════════════════════════════
//  NOTEBOOK STATE
// ════════════════════════════════════════════════════════════════════════

let currentNbId   = null;
let currentConvId = null;
let nbBusy        = false;
let nbSseCtrl     = null;   // AbortController for active SSE stream

const nbListEl       = document.getElementById("nb-list");
const nbNameDisplay  = document.getElementById("nb-name-display");
const nbSourcesWrap  = document.getElementById("nb-sources-wrap");
const nbConvWrap     = document.getElementById("nb-conv-wrap");
const nbSourceList   = document.getElementById("nb-source-list");
const nbConvList     = document.getElementById("nb-conv-list");
const nbPlaceholder  = document.getElementById("nb-placeholder");
const nbChatWrap     = document.getElementById("nb-chat-wrap");
const nbMessagesEl   = document.getElementById("nb-messages");
const nbQueryInput   = document.getElementById("nb-query-input");
const nbSendBtn      = document.getElementById("nb-send-btn");
const nbClearBtn     = document.getElementById("nb-clear-btn");
const nbCitationsBar = document.getElementById("nb-citations-bar");
const nbCitationsList= document.getElementById("nb-citations-list");

// ── Notebook list ────────────────────────────────────────────────────

async function loadNotebooks() {
    const res = await fetch("/api/notebooks");
    const data = await res.json();
    renderNotebookList(data.notebooks || []);
}

function renderNotebookList(notebooks) {
    if (!notebooks.length) {
        nbListEl.innerHTML = '<div class="doc-empty">No notebooks yet. Click ＋ to create one.</div>';
        return;
    }
    nbListEl.innerHTML = notebooks.map(nb => `
        <div class="nb-item${nb.id === currentNbId ? " active" : ""}" data-id="${nb.id}">
            <span class="nb-item-name">${escapeHtml(nb.name)}</span>
            <span class="nb-item-count" style="color:var(--text-secondary);font-size:11px;margin-right:6px">${nb.source_count || 0}</span>
            <button class="nb-item-del" data-id="${nb.id}" title="Delete notebook">×</button>
        </div>
    `).join("");

    nbListEl.querySelectorAll(".nb-item").forEach(el => {
        el.addEventListener("click", (e) => {
            if (e.target.classList.contains("nb-item-del")) return;
            selectNotebook(el.dataset.id, notebooks.find(n => n.id === el.dataset.id)?.name || "Notebook");
        });
    });
    nbListEl.querySelectorAll(".nb-item-del").forEach(btn => {
        btn.addEventListener("click", (e) => { e.stopPropagation(); deleteNotebook(btn.dataset.id); });
    });
}

async function selectNotebook(nbId, name) {
    currentNbId   = nbId;
    currentConvId = null;

    nbNameDisplay.textContent = name;
    nbSourcesWrap.style.display = "";
    nbConvWrap.style.display    = "";
    nbPlaceholder.style.display = "none";
    nbChatWrap.style.display    = "none";
    nbCitationsBar.style.display= "none";

    // highlight
    nbListEl.querySelectorAll(".nb-item").forEach(el => {
        el.classList.toggle("active", el.dataset.id === nbId);
    });

    await Promise.all([loadSources(nbId), loadConversations(nbId)]);
}

document.getElementById("nb-new-btn").addEventListener("click", async () => {
    const name = prompt("Notebook name:", "New Notebook");
    if (!name) return;
    const res = await fetch("/api/notebooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
    });
    const data = await res.json();
    await loadNotebooks();
    selectNotebook(data.id, data.name);
});

async function deleteNotebook(nbId) {
    if (!confirm("Delete this notebook and all its sources?")) return;
    await fetch(`/api/notebooks/${nbId}`, { method: "DELETE" });
    if (currentNbId === nbId) {
        currentNbId = null;
        nbNameDisplay.textContent = "Notebooks";
        nbSourcesWrap.style.display = "none";
        nbConvWrap.style.display    = "none";
        nbChatWrap.style.display    = "none";
        nbPlaceholder.style.display = "";
    }
    loadNotebooks();
}

// ── Sources ──────────────────────────────────────────────────────────

let _srcPollTimers = {};

async function loadSources(nbId) {
    const res  = await fetch(`/api/notebooks/${nbId}/sources`);
    const data = await res.json();
    renderSources(nbId, data.sources || []);
}

function renderSources(nbId, sources) {
    if (!sources.length) {
        nbSourceList.innerHTML = '<div class="doc-empty" style="font-size:12px">No sources yet.</div>';
        return;
    }
    nbSourceList.innerHTML = sources.map(s => `
        <div class="nb-source-item" data-src-id="${s.id}">
            <span class="src-dot ${s.status}"></span>
            <span class="src-name" title="${escapeHtml(s.title || s.filename || s.url || '')}">${escapeHtml(s.title || s.filename || s.url || 'Source')}</span>
            <button class="src-del" data-id="${s.id}" title="Remove">×</button>
        </div>
    `).join("");

    nbSourceList.querySelectorAll(".src-del").forEach(btn => {
        btn.addEventListener("click", () => deleteSource(nbId, btn.dataset.id));
    });

    // poll for processing sources
    sources.filter(s => s.status === "pending" || s.status === "processing").forEach(s => {
        if (!_srcPollTimers[s.id]) {
            _srcPollTimers[s.id] = setInterval(async () => {
                const r2 = await fetch(`/api/notebooks/${nbId}/sources/${s.id}`);
                const sd = await r2.json();
                if (sd.status === "done" || sd.status === "failed") {
                    clearInterval(_srcPollTimers[s.id]);
                    delete _srcPollTimers[s.id];
                    loadSources(nbId);
                }
            }, 2000);
        }
    });
}

async function deleteSource(nbId, srcId) {
    await fetch(`/api/notebooks/${nbId}/sources/${srcId}`, { method: "DELETE" });
    loadSources(nbId);
}

// File upload
document.getElementById("nb-file-input").addEventListener("change", async (e) => {
    if (!currentNbId || !e.target.files.length) return;
    const form = new FormData();
    for (const f of e.target.files) form.append("file", f);
    // upload each file separately (API takes one file per request)
    for (const f of e.target.files) {
        const fd = new FormData();
        fd.append("file", f);
        await fetch(`/api/notebooks/${currentNbId}/upload`, { method: "POST", body: fd });
    }
    e.target.value = "";
    setTimeout(() => loadSources(currentNbId), 500);
});

// URL add
document.getElementById("nb-url-btn").addEventListener("click", async () => {
    const url = document.getElementById("nb-url-input").value.trim();
    if (!url || !currentNbId) return;
    await fetch(`/api/notebooks/${currentNbId}/add-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
    });
    document.getElementById("nb-url-input").value = "";
    setTimeout(() => loadSources(currentNbId), 500);
});

// ── Conversations ─────────────────────────────────────────────────────

async function loadConversations(nbId) {
    const res  = await fetch(`/api/notebooks/${nbId}/conversations`);
    const data = await res.json();
    renderConversations(nbId, data.conversations || []);
}

function renderConversations(nbId, convs) {
    if (!convs.length) {
        nbConvList.innerHTML = '<div class="doc-empty" style="font-size:12px">No conversations yet.</div>';
        return;
    }
    nbConvList.innerHTML = convs.map(c => `
        <div class="nb-conv-item${c.id === currentConvId ? " active" : ""}" data-id="${c.id}">
            <span class="nb-conv-title">${escapeHtml(c.title)}</span>
        </div>
    `).join("");

    nbConvList.querySelectorAll(".nb-conv-item").forEach(el => {
        el.addEventListener("click", () => selectConversation(nbId, el.dataset.id,
            convs.find(c => c.id === el.dataset.id)?.title || "Conversation"));
    });
}

async function selectConversation(nbId, convId, title) {
    currentConvId = convId;
    nbChatWrap.style.display = "";
    nbPlaceholder.style.display = "none";

    nbConvList.querySelectorAll(".nb-conv-item").forEach(el => {
        el.classList.toggle("active", el.dataset.id === convId);
    });

    // load message history
    const res  = await fetch(`/api/notebooks/${nbId}/conversations/${convId}/messages`);
    const data = await res.json();
    nbMessagesEl.innerHTML = "";
    for (const m of data.messages || []) {
        if (m.role === "user") {
            nbAddMessage("user", m.content);
        } else {
            nbAddMessage("assistant", m.content, m.citations || []);
        }
    }
    nbScrollBottom();
}

document.getElementById("nb-conv-new-btn").addEventListener("click", async () => {
    if (!currentNbId) return;
    const title = prompt("Conversation title:", "New Conversation");
    if (!title) return;
    const res = await fetch(`/api/notebooks/${currentNbId}/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
    });
    const data = await res.json();
    await loadConversations(currentNbId);
    selectConversation(currentNbId, data.id, data.title);
});

// ── Notebook chat ─────────────────────────────────────────────────────

function nbAddMessage(role, content, citations = []) {
    const div = document.createElement("div");
    div.className = `message ${role}`;
    const rendered = role === "assistant" ? nbRenderAnswer(content) : escapeHtml(content);
    div.innerHTML = `<div class="bubble">${rendered}</div>`;
    nbMessagesEl.appendChild(div);
    nbScrollBottom();

    if (citations.length) renderCitationsBar(citations);
    return div;
}

function nbRenderAnswer(text) {
    // render [N] inline citation refs
    let html = renderMarkdown(text);
    html = html.replace(/\[(\d+)\]/g, '<span class="cite-ref">$1</span>');
    return html;
}

function renderCitationsBar(citations) {
    if (!citations || !citations.length) {
        nbCitationsBar.style.display = "none";
        return;
    }
    nbCitationsBar.style.display = "";
    nbCitationsList.innerHTML = citations.map(c => `
        <span class="citation-chip" title="${escapeHtml(c.snippet || '')}">
            <span class="citation-num">[${c.number}]</span>
            <span class="citation-title">${escapeHtml(c.source_title || 'Source')}</span>
            ${c.page_number ? `<span class="citation-page">p.${c.page_number}</span>` : ''}
        </span>
    `).join("");
}

function nbSetBusy(busy) {
    nbBusy = busy;
    nbSendBtn.disabled   = busy;
    nbQueryInput.disabled = busy;
    nbSendBtn.classList.toggle("disabled", busy);
}

function nbScrollBottom() {
    nbMessagesEl.scrollTop = nbMessagesEl.scrollHeight;
}

async function nbSendMessage(text) {
    if (!text.trim() || nbBusy || !currentNbId || !currentConvId) return;

    nbAddMessage("user", text);
    nbQueryInput.value = "";
    nbQueryInput.style.height = "auto";
    nbSetBusy(true);

    // add streaming bubble
    const bubbleDiv = document.createElement("div");
    bubbleDiv.className = "message assistant";
    bubbleDiv.innerHTML = '<div class="bubble stream-cursor"></div>';
    nbMessagesEl.appendChild(bubbleDiv);
    nbScrollBottom();
    const bubbleEl = bubbleDiv.querySelector(".bubble");

    // abort any prior stream
    if (nbSseCtrl) nbSseCtrl.abort();
    nbSseCtrl = new AbortController();

    let accumulated = "";
    let lastCitations = [];

    try {
        const res = await fetch(
            `/api/notebooks/${currentNbId}/conversations/${currentConvId}/chat/stream`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: text }),
                signal: nbSseCtrl.signal,
            }
        );

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split("\n");
            buf = lines.pop(); // keep incomplete last line

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                try {
                    const evt = JSON.parse(line.slice(6));
                    if (evt.type === "citations") {
                        lastCitations = evt.citations;
                        renderCitationsBar(lastCitations);
                    } else if (evt.type === "delta") {
                        accumulated += evt.text;
                        bubbleEl.innerHTML = nbRenderAnswer(accumulated);
                        bubbleEl.classList.add("stream-cursor");
                        nbScrollBottom();
                    } else if (evt.type === "done" || evt.type === "error") {
                        bubbleEl.classList.remove("stream-cursor");
                        if (evt.type === "error") {
                            bubbleEl.innerHTML += `<span style="color:var(--red)"> [${escapeHtml(evt.message)}]</span>`;
                        }
                    }
                } catch {}
            }
        }
    } catch (err) {
        if (err.name !== "AbortError") {
            bubbleEl.classList.remove("stream-cursor");
            bubbleEl.innerHTML += `<span style="color:var(--red)"> [Network error: ${escapeHtml(err.message)}]</span>`;
        }
    } finally {
        bubbleEl.classList.remove("stream-cursor");
        nbSetBusy(false);
        nbSseCtrl = null;
    }
}

nbSendBtn.addEventListener("click", () => nbSendMessage(nbQueryInput.value));

nbQueryInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        nbSendMessage(nbQueryInput.value);
    }
});

nbQueryInput.addEventListener("input", () => {
    nbQueryInput.style.height = "auto";
    nbQueryInput.style.height = Math.min(nbQueryInput.scrollHeight, 120) + "px";
});

nbClearBtn.addEventListener("click", () => {
    nbMessagesEl.innerHTML = "";
    nbCitationsBar.style.display = "none";
});

// ── Mobile notebook sidebar ───────────────────────────────────────────

const nbMenuBtn        = document.getElementById("nb-menu-btn");
const nbSidebarOverlay = document.getElementById("nb-sidebar-overlay");
const nbColumn         = document.getElementById("panel-notebook");
const nbOpenBtn        = document.getElementById("nb-open-btn");

if (window.innerWidth > 1200) {
    nbColumn.classList.remove("closed");
}

function toggleNbSidebar() {
    const isClosed = nbColumn.classList.toggle("closed");
    nbSidebarOverlay.classList.toggle("open", !isClosed);
}

nbMenuBtn.addEventListener("click", toggleNbSidebar);
nbSidebarOverlay.addEventListener("click", toggleNbSidebar);
nbOpenBtn.addEventListener("click", () => {
    nbColumn.classList.remove("closed");
    nbSidebarOverlay.classList.add("open");
});
