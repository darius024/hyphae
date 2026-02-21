const messagesEl = document.getElementById("messages");
const queryInput = document.getElementById("query-input");
const sendBtn = document.getElementById("send-btn");
const voiceBtn = document.getElementById("voice-btn");
const docListEl = document.getElementById("doc-list-items");
const fileInput = document.getElementById("file-input");
const uploadBtn = document.getElementById("upload-btn");

let isRecording = false;
let mediaRecorder = null;

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

function addMessage(role, content, meta) {
    const div = document.createElement("div");
    div.className = `message ${role}`;

    const rendered = role === "assistant" ? renderMarkdown(content) : escapeHtml(content);
    let html = `<div class="bubble">${rendered}</div>`;

    if (meta) {
        const badge = meta.source === "on-device"
            ? '<span class="badge local">LOCAL</span>'
            : '<span class="badge cloud">CLOUD</span>';
        html += `<div class="meta">${badge} <span>${meta.routing_ms}ms</span></div>`;
    }

    div.innerHTML = html;
    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
}

function addToolResults(functionCalls, toolResults) {
    if (!toolResults || toolResults.length === 0) return;

    const div = document.createElement("div");
    div.className = "message assistant";

    let html = "";
    for (const tr of toolResults) {
        const argsStr = JSON.stringify(tr.arguments, null, 2);
        const resultStr = JSON.stringify(tr.result, null, 2);

        html += `<div class="tool-call">
            <span class="tool-name">${escapeHtml(tr.tool)}</span>
            <div class="tool-args">${escapeHtml(argsStr)}</div>
        </div>`;
        html += `<div class="tool-result">${escapeHtml(resultStr)}</div>`;
    }

    div.innerHTML = html;
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
    if (!text.trim()) return;

    addMessage("user", text);
    queryInput.value = "";
    queryInput.style.height = "auto";

    const thinking = addThinking();

    try {
        const res = await fetch("/api/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text }),
        });
        const data = await res.json();
        removeThinking();

        if (data.error) {
            addMessage("assistant", `Error: ${data.error}`);
            return;
        }

        if (data.answer) {
            addMessage("assistant", data.answer, {
                source: data.source,
                routing_ms: data.routing_ms,
            });
        } else if (data.function_calls.length > 0) {
            addMessage("assistant", `Called ${data.function_calls.map(fc => fc.name).join(", ")}`, {
                source: data.source,
                routing_ms: data.routing_ms,
            });
        } else {
            addMessage("assistant", "I couldn't find a relevant tool for that query. Try rephrasing?", {
                source: data.source,
                routing_ms: data.routing_ms,
            });
        }

        addToolResults(data.function_calls, data.tool_results);
    } catch (err) {
        removeThinking();
        addMessage("assistant", `Network error: ${err.message}`);
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
        mediaRecorder = new MediaRecorder(stream);
        const chunks = [];

        mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(t => t.stop());
            const blob = new Blob(chunks, { type: "audio/wav" });
            await sendVoice(blob);
        };

        mediaRecorder.start();
        isRecording = true;
        voiceBtn.classList.add("recording");
        voiceBtn.textContent = "⏹";
    } catch (err) {
        addMessage("assistant", `Microphone access denied: ${err.message}`);
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
    isRecording = false;
    voiceBtn.classList.remove("recording");
    voiceBtn.textContent = "🎤";
}

async function sendVoice(blob) {
    const thinking = addThinking();
    const form = new FormData();
    form.append("audio", blob, "recording.wav");

    try {
        const res = await fetch("/api/voice", { method: "POST", body: form });
        const data = await res.json();
        removeThinking();

        if (data.error) {
            addMessage("assistant", `Voice error: ${data.error}`);
            return;
        }

        addMessage("user", `🎤 "${data.transcript}"`);

        if (data.answer) {
            addMessage("assistant", data.answer, {
                source: data.source,
                routing_ms: data.routing_ms,
            });
        } else if (data.function_calls.length > 0) {
            addMessage("assistant", `Called ${data.function_calls.map(fc => fc.name).join(", ")}`, {
                source: data.source,
                routing_ms: data.routing_ms,
            });
        } else {
            addMessage("assistant", "I couldn't process that. Try again?", {
                source: data.source,
                routing_ms: data.routing_ms,
            });
        }

        addToolResults(data.function_calls, data.tool_results);
    } catch (err) {
        removeThinking();
        addMessage("assistant", `Voice error: ${err.message}`);
    }
}

// ── Documents ───────────────────────────────────────────────────────

async function loadDocuments() {
    try {
        const res = await fetch("/api/documents");
        const data = await res.json();
        renderDocuments(data.documents);
    } catch {
        docListEl.innerHTML = '<div class="doc-item"><span class="name">Failed to load</span></div>';
    }
}

function renderDocuments(docs) {
    if (!docs || docs.length === 0) {
        docListEl.innerHTML = '<div class="doc-item"><span class="name" style="color:var(--text-secondary)">No documents yet</span></div>';
        return;
    }

    docListEl.innerHTML = docs.map(d => `
        <div class="doc-item">
            <span class="name">${escapeHtml(d.name)}</span>
            <span class="size">${d.size_kb} KB</span>
            <button class="remove-btn" onclick="removeDoc('${escapeHtml(d.name)}')" title="Remove">×</button>
        </div>
    `).join("");
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

    try {
        const res = await fetch("/api/upload", { method: "POST", body: form });
        const data = await res.json();
        loadDocuments();
        const count = data.uploaded.filter(u => u.added).length;
        if (count > 0) {
            addMessage("assistant", `Uploaded ${count} document(s) to corpus.`);
        }
    } catch (err) {
        addMessage("assistant", `Upload failed: ${err.message}`);
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

// ── Init ────────────────────────────────────────────────────────────
loadDocuments();
