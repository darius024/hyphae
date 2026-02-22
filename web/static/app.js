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
            <strong>Welcome back, researcher.</strong> Your documents are loaded and ready. Ask questions,
            search your corpus, compare papers, or generate hypotheses.
            <span class="privacy-note">Confidential data stays on-device — cloud is only used when you need external resources.</span>
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
    let privacyBadge = "";
    if (meta.data_local === true) {
        privacyBadge = '<span class="badge privacy-local" title="No data left your device"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> PRIVATE</span>';
    } else if (meta.data_local === false) {
        privacyBadge = '<span class="badge privacy-cloud" title="Data sent to Gemini for synthesis"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg> CLOUD</span>';
    }
    return `<div class="meta">${routeBadge}${confBadge}${privacyBadge} <span>${meta.routing_ms}ms</span></div>`;
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

        const meta = {
            source: data.source, routing_ms: data.routing_ms,
            confidence: data.confidence, data_local: data.data_local,
        };
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

function docTypeIcon(doc) {
    if (doc.has_pdf || doc.type === "pdf") {
        return '<svg class="doc-type-icon pdf" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
    }
    if (doc.type === "md") {
        return '<svg class="doc-type-icon md" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
    }
    return '<svg class="doc-type-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>';
}

function sensitivityBadge(doc) {
    const isConf = doc.sensitivity === "confidential";
    if (isConf) {
        return `<button class="sens-btn sens-conf" data-doc="${escapeHtml(doc.name)}" data-level="confidential" title="Confidential — click to make shareable"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></button>`;
    }
    return `<button class="sens-btn sens-share" data-doc="${escapeHtml(doc.name)}" data-level="shareable" title="Shareable — click to mark confidential"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 5-5 5 5 0 0 1 5 5"/></svg></button>`;
}

function renderDocuments(docs) {
    if (!docs || docs.length === 0) {
        docListEl.innerHTML = '<div class="doc-empty">No documents yet. Upload PDFs or text files to get started.</div>';
        return;
    }

    docListEl.innerHTML = docs.map(d => `
        <div class="doc-item">
            ${docTypeIcon(d)}
            <span class="name doc-preview-link" data-doc="${escapeHtml(d.name)}">${escapeHtml(d.name)}</span>
            ${d.has_pdf ? '<span class="badge doc-badge-pdf">PDF</span>' : ''}
            ${sensitivityBadge(d)}
            <span class="size">${d.size_kb} KB</span>
            <button class="remove-btn" onclick="removeDoc('${escapeHtml(d.name)}')" title="Remove">×</button>
        </div>
    `).join("");

    docListEl.querySelectorAll(".doc-preview-link").forEach(el => {
        el.addEventListener("click", () => previewDoc(el.dataset.doc));
    });

    docListEl.querySelectorAll(".sens-btn").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const name = btn.dataset.doc;
            const newLevel = btn.dataset.level === "confidential" ? "shareable" : "confidential";
            try {
                await fetch(`/api/sensitivity/${encodeURIComponent(name)}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ level: newLevel }),
                });
                loadDocuments();
            } catch {}
        });
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

// ── Tool panel collapse toggle ──────────────────────────────────────
const toolPanelToggle = document.getElementById("tool-panel-toggle");
if (toolPanelToggle) {
    toolPanelToggle.addEventListener("click", () => {
        toolPanelToggle.classList.toggle("collapsed");
        toolListEl.classList.toggle("collapsed");
    });
}

// ── Quick research prompts ─────────────────────────────────────────

function loadQuickPrompts() {
    if (!quickButtonsEl) return;
    const prompts = [
        {
            title: "Summary with citations",
            hint: "Scan corpus and cite source filenames",
            text: "Summarize the corpus notes with inline citations [filename] and highlight gaps to investigate next.",
            icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>',
        },
        {
            title: "Compare documents",
            hint: "Contrast two files on a topic",
            text: "Compare polymer_synthesis_notes.txt vs battery_cycling_log.txt on conductive additives; output key differences with citations.",
            icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
        },
        {
            title: "Design experiment",
            hint: "Propose next steps and metrics",
            text: "Propose the next experiment to improve conductivity >1 S/cm while keeping self-healing >85%; include materials, protocol steps, and measurement plan with citations.",
            icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3v11"/><path d="M15 3v4"/><path d="M9 14l-4 7h14l-4-7"/><circle cx="9" cy="14" r="2"/></svg>',
        },
        {
            title: "Literature + local",
            hint: "Blend local corpus with web search",
            text: "Search literature on PEDOT:PSS self-healing hydrogels and combine with local corpus findings; cite online papers as [L1], [L2] and local files by name.",
            icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
        }
    ];

    quickButtonsEl.innerHTML = prompts.map(p => `
        <button class="quick-btn" data-text="${escapeHtml(p.text)}">
            <strong><span class="quick-icon">${p.icon}</span> ${escapeHtml(p.title)}</strong>
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
    uploadBtn.querySelector("span:not(.upload-hint)").textContent = "Uploading…";

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
        uploadBtn.querySelector("span:not(.upload-hint)").textContent = "Upload documents";
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

// ── Route prediction indicator ──────────────────────────────────────

const routeIndicator = document.getElementById("route-indicator");
let _classifyTimer = null;

function updateRouteIndicator(text) {
    if (!text.trim()) {
        routeIndicator.innerHTML = "";
        routeIndicator.className = "route-indicator";
        return;
    }
    clearTimeout(_classifyTimer);
    _classifyTimer = setTimeout(async () => {
        try {
            const res = await fetch("/api/classify", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: text }),
            });
            const data = await res.json();
            if (data.route === "local") {
                routeIndicator.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> Stays on-device';
                routeIndicator.className = "route-indicator route-local";
            } else {
                routeIndicator.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg> Will use Gemini cloud';
                routeIndicator.className = "route-indicator route-cloud";
            }
        } catch {}
    }, 300);
}

queryInput.addEventListener("input", () => updateRouteIndicator(queryInput.value));

// ── Privacy audit log ───────────────────────────────────────────────

const privacyLogOverlay = document.getElementById("privacy-log-overlay");
const privacyLogBody = document.getElementById("privacy-log-body");

document.getElementById("privacy-log-btn").addEventListener("click", loadPrivacyLog);
document.getElementById("privacy-log-close").addEventListener("click", () => {
    privacyLogOverlay.classList.remove("open");
});
privacyLogOverlay.addEventListener("click", (e) => {
    if (e.target === privacyLogOverlay) privacyLogOverlay.classList.remove("open");
});

async function loadPrivacyLog() {
    privacyLogOverlay.classList.add("open");
    privacyLogBody.innerHTML = '<div class="doc-empty">Loading…</div>';
    try {
        const res = await fetch("/api/privacy-log");
        const data = await res.json();
        const entries = data.entries || [];
        if (!entries.length) {
            privacyLogBody.innerHTML = '<div class="doc-empty">No queries yet. Your audit log will appear here as you use Hyphae.</div>';
            return;
        }
        privacyLogBody.innerHTML = `
            <div class="plog-summary">
                <div class="plog-stat plog-stat-local">
                    <strong>${entries.filter(e => e.data_local).length}</strong>
                    <span>Local queries</span>
                </div>
                <div class="plog-stat plog-stat-cloud">
                    <strong>${entries.filter(e => !e.data_local).length}</strong>
                    <span>Cloud queries</span>
                </div>
                <div class="plog-stat">
                    <strong>${entries.length}</strong>
                    <span>Total</span>
                </div>
            </div>
            <div class="plog-entries">
                ${entries.slice().reverse().map(e => {
                    const time = new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                    const badge = e.data_local
                        ? '<span class="badge privacy-local"><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> PRIVATE</span>'
                        : '<span class="badge privacy-cloud"><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg> CLOUD</span>';
                    const tools = (e.tools || []).map(t => `<code>${escapeHtml(t)}</code>`).join(" ");
                    return `<div class="plog-entry">
                        <span class="plog-time">${time}</span>
                        ${badge}
                        <span class="plog-query">${escapeHtml(e.query)}</span>
                        <span class="plog-tools">${tools}</span>
                        <span class="plog-ms">${e.routing_ms}ms</span>
                    </div>`;
                }).join("")}
            </div>
        `;
    } catch (err) {
        privacyLogBody.innerHTML = `<div class="doc-empty">Failed to load: ${escapeHtml(err.message)}</div>`;
    }
}

// ── Document preview modal ──────────────────────────────────────────

const previewOverlay = document.getElementById("preview-overlay");
const previewTitle = document.getElementById("preview-title");
const previewBody = document.getElementById("preview-body");
const previewPdf = document.getElementById("preview-pdf");
const previewTabText = document.getElementById("preview-tab-text");
const previewTabPdf = document.getElementById("preview-tab-pdf");
const previewDownload = document.getElementById("preview-download");

let _previewHasPdf = false;
let _previewPdfName = null;

function showPreviewTab(tab) {
    if (tab === "pdf" && _previewHasPdf) {
        previewBody.style.display = "none";
        previewPdf.style.display = "";
        previewTabPdf.classList.add("active");
        previewTabText.classList.remove("active");
        if (!previewPdf.src || previewPdf.src === "about:blank") {
            previewPdf.src = `/api/documents/${encodeURIComponent(_previewPdfName)}/raw`;
        }
    } else {
        previewBody.style.display = "";
        previewPdf.style.display = "none";
        previewTabText.classList.add("active");
        previewTabPdf.classList.remove("active");
    }
}

async function previewDoc(name) {
    previewTitle.textContent = name;
    previewBody.textContent = "Loading…";
    previewPdf.src = "about:blank";
    previewPdf.style.display = "none";
    previewBody.style.display = "";
    previewTabPdf.style.display = "none";
    previewTabText.classList.add("active");
    previewTabPdf.classList.remove("active");
    _previewHasPdf = false;
    _previewPdfName = null;
    previewOverlay.classList.add("open");

    try {
        const res = await fetch(`/api/documents/${encodeURIComponent(name)}`);
        const data = await res.json();
        if (data.error) {
            previewBody.textContent = `Error: ${data.error}`;
            return;
        }
        previewBody.textContent = data.preview;
        previewTitle.textContent = `${name} (${data.size_kb} KB)`;

        if (data.has_pdf && data.pdf_name) {
            _previewHasPdf = true;
            _previewPdfName = data.pdf_name;
            previewTabPdf.style.display = "";
            previewDownload.href = `/api/documents/${encodeURIComponent(data.pdf_name)}/raw`;
            previewDownload.download = data.pdf_name;
            showPreviewTab("pdf");
        } else {
            previewDownload.href = `/api/documents/${encodeURIComponent(name)}/raw`;
            previewDownload.download = name;
        }
    } catch (err) {
        previewBody.textContent = `Failed to load: ${err.message}`;
    }
}

function closePreview() {
    previewOverlay.classList.remove("open");
    previewPdf.src = "about:blank";
}

previewTabText.addEventListener("click", () => showPreviewTab("text"));
previewTabPdf.addEventListener("click", () => showPreviewTab("pdf"));
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
const mainInputArea  = document.getElementById("main-input-area");

function enterNotebookChat(nbName, convTitle) {
    messagesEl.style.display = "none";
    mainInputArea.style.display = "none";
    nbChatWrap.style.display = "";
    document.getElementById("nb-chat-title").textContent = nbName + " \u2014 " + convTitle;
}

function exitNotebookChat() {
    nbChatWrap.style.display = "none";
    messagesEl.style.display = "";
    mainInputArea.style.display = "";
    currentConvId = null;
    nbMessagesEl.innerHTML = "";
    nbCitationsBar.style.display = "none";
    nbConvList.querySelectorAll(".nb-conv-item").forEach(el => el.classList.remove("active"));
}

document.getElementById("nb-chat-back").addEventListener("click", exitNotebookChat);

// ── Notebook list ────────────────────────────────────────────────────

async function loadNotebooks() {
    const res = await fetch("/api/notebooks");
    const data = await res.json();
    renderNotebookList(data.notebooks || []);
}

function renderNotebookList(notebooks) {
    if (!notebooks.length) {
        nbListEl.innerHTML = `<div class="nb-empty-state">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--text-tertiary);margin-bottom:8px"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg>
            <p>No notebooks yet</p>
            <button class="nb-empty-create" onclick="document.getElementById('nb-new-btn').click()">Create your first notebook</button>
        </div>`;
        return;
    }
    nbListEl.innerHTML = notebooks.map(nb => `
        <div class="nb-item${nb.id === currentNbId ? " active" : ""}" data-id="${nb.id}">
            <svg class="nb-item-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg>
            <span class="nb-item-name">${escapeHtml(nb.name)}</span>
            <span class="nb-item-count">${nb.source_count || 0}</span>
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
    currentNbId = nbId;
    if (currentConvId) exitNotebookChat();
    currentConvId = null;

    nbNameDisplay.textContent = name;
    nbSourcesWrap.style.display = "";
    nbConvWrap.style.display    = "";
    nbPlaceholder.style.display = "none";

    nbListEl.querySelectorAll(".nb-item").forEach(el => {
        el.classList.toggle("active", el.dataset.id === nbId);
    });

    await Promise.all([loadSources(nbId), loadConversations(nbId)]);
}

// ── Notebook creation modal ───────────────────────────────────────────
const nbCreateOverlay = document.getElementById("nb-create-overlay");
const nbCreateName = document.getElementById("nb-create-name");
const nbCreateDesc = document.getElementById("nb-create-desc");
const nbCreateSubmit = document.getElementById("nb-create-submit");

function openNbCreateModal() {
    nbCreateName.value = "";
    nbCreateDesc.value = "";
    nbCreateOverlay.classList.add("open");
    setTimeout(() => nbCreateName.focus(), 100);
}

function closeNbCreateModal() {
    nbCreateOverlay.classList.remove("open");
}

async function submitNewNotebook() {
    const name = nbCreateName.value.trim();
    if (!name) { nbCreateName.focus(); return; }
    nbCreateSubmit.disabled = true;
    nbCreateSubmit.textContent = "Creating…";
    try {
        const res = await fetch("/api/notebooks", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
        });
        const data = await res.json();
        closeNbCreateModal();
        await loadNotebooks();
        selectNotebook(data.id, data.name);
    } finally {
        nbCreateSubmit.disabled = false;
        nbCreateSubmit.textContent = "Create notebook";
    }
}

document.getElementById("nb-new-btn").addEventListener("click", openNbCreateModal);
document.getElementById("nb-create-close").addEventListener("click", closeNbCreateModal);
nbCreateOverlay.addEventListener("click", (e) => { if (e.target === nbCreateOverlay) closeNbCreateModal(); });
nbCreateSubmit.addEventListener("click", submitNewNotebook);
nbCreateName.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submitNewNotebook(); } });

async function deleteNotebook(nbId) {
    if (!confirm("Delete this notebook and all its sources?")) return;
    await fetch(`/api/notebooks/${nbId}`, { method: "DELETE" });
    if (currentNbId === nbId) {
        if (currentConvId) exitNotebookChat();
        currentNbId = null;
        nbNameDisplay.textContent = "Notebooks";
        nbSourcesWrap.style.display = "none";
        nbConvWrap.style.display    = "none";
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

let _convCache = [];

function relativeTime(dateStr) {
    if (!dateStr) return "";
    const d = new Date(dateStr);
    const now = Date.now();
    const sec = Math.floor((now - d.getTime()) / 1000);
    if (sec < 60) return "now";
    const min = Math.floor(sec / 60);
    if (min < 60) return min + "m";
    const hr = Math.floor(min / 60);
    if (hr < 24) return hr + "h";
    const day = Math.floor(hr / 24);
    if (day < 7) return day + "d";
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

async function loadConversations(nbId) {
    const res  = await fetch(`/api/notebooks/${nbId}/conversations`);
    const data = await res.json();
    _convCache = data.conversations || [];
    renderConversations(nbId, _convCache);
}

function renderConversations(nbId, convs) {
    if (!convs.length) {
        nbConvList.innerHTML = `<div class="conv-empty">
            <p>No conversations yet</p>
            <button class="conv-empty-create" onclick="document.getElementById('nb-conv-new-btn').click()">Start a conversation</button>
        </div>`;
        return;
    }
    nbConvList.innerHTML = convs.map(c => `
        <div class="nb-conv-item${c.id === currentConvId ? " active" : ""}" data-id="${c.id}" data-title="${escapeHtml(c.title)}">
            <svg class="conv-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <span class="nb-conv-title">${escapeHtml(c.title)}</span>
            <span class="conv-time">${relativeTime(c.updated_at)}</span>
            <div class="conv-actions">
                <button class="conv-action-btn conv-rename-btn" data-id="${c.id}" title="Rename">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
                </button>
                <button class="conv-action-btn conv-delete-btn" data-id="${c.id}" title="Delete">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
            </div>
        </div>
    `).join("");

    nbConvList.querySelectorAll(".nb-conv-item").forEach(el => {
        el.addEventListener("click", (e) => {
            if (e.target.closest(".conv-actions")) return;
            selectConversation(nbId, el.dataset.id, el.dataset.title);
        });
        el.addEventListener("dblclick", (e) => {
            if (e.target.closest(".conv-actions")) return;
            startConvRename(nbId, el.dataset.id);
        });
    });

    nbConvList.querySelectorAll(".conv-rename-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            startConvRename(nbId, btn.dataset.id);
        });
    });

    nbConvList.querySelectorAll(".conv-delete-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            deleteConversation(nbId, btn.dataset.id);
        });
    });
}

async function selectConversation(nbId, convId, title) {
    currentConvId = convId;
    enterNotebookChat(nbNameDisplay.textContent, title);

    nbConvList.querySelectorAll(".nb-conv-item").forEach(el => {
        el.classList.toggle("active", el.dataset.id === convId);
    });

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

function startConvRename(nbId, convId) {
    const item = nbConvList.querySelector(`.nb-conv-item[data-id="${convId}"]`);
    if (!item) return;
    const titleEl = item.querySelector(".nb-conv-title");
    const oldTitle = item.dataset.title;

    const input = document.createElement("input");
    input.type = "text";
    input.className = "conv-rename-input";
    input.value = oldTitle;
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    item.querySelector(".conv-actions").style.display = "none";
    item.querySelector(".conv-time").style.display = "none";

    async function commitRename() {
        const newTitle = input.value.trim();
        if (!newTitle || newTitle === oldTitle) {
            loadConversations(nbId);
            return;
        }
        try {
            await fetch(`/api/notebooks/${nbId}/conversations/${convId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: newTitle }),
            });
            if (currentConvId === convId) {
                document.getElementById("nb-chat-title").textContent =
                    nbNameDisplay.textContent + " \u2014 " + newTitle;
            }
        } catch {}
        loadConversations(nbId);
    }

    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); commitRename(); }
        if (e.key === "Escape") { loadConversations(nbId); }
    });
    input.addEventListener("blur", commitRename);
}

async function deleteConversation(nbId, convId) {
    if (!confirm("Delete this conversation?")) return;
    try {
        await fetch(`/api/notebooks/${nbId}/conversations/${convId}`, { method: "DELETE" });
    } catch {}
    if (currentConvId === convId) {
        exitNotebookChat();
    }
    loadConversations(nbId);
}

document.getElementById("nb-conv-new-btn").addEventListener("click", async () => {
    if (!currentNbId) return;
    const title = "New conversation";
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
