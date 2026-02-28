// ── Global fetch interceptor — auto-attach auth token ────────────────
{
    const _originalFetch = window.fetch;
    window.fetch = function (input, init) {
        const token = localStorage.getItem("hyphae_auth_token");
        if (token) {
            init = init || {};
            const headers = init.headers instanceof Headers
                ? init.headers
                : new Headers(init.headers || {});
            if (!headers.has("Authorization")) {
                headers.set("Authorization", `Bearer ${token}`);
            }
            init.headers = headers;
        }
        return _originalFetch.call(this, input, init);
    };
}

const toolListEl = document.getElementById("tool-list");
// Doc search input (used for Cmd/Ctrl+K). Fallback to any .doc-search field.
const docSearchInput = document.getElementById("nb-url-input") || document.querySelector(".doc-search");
const quickButtonsEl = document.getElementById("quick-buttons");
const welcomeQuickButtonsEl = document.getElementById("welcome-quick-buttons");

let isRecording = false;
let mediaRecorder = null;

// ── Tool result rendering (uses nbMessagesEl, nbScrollBottom from core) ──

function nbAddToolResults(functionCalls, toolResults) {
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
    nbMessagesEl.appendChild(div);
    nbScrollBottom();
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

// Friendly prompt starters for each tool
const TOOL_PROMPTS = {
    search_papers:       "Search my notes for…",
    summarise_notes:     "Summarise my notes on…",
    create_note:         "Save a note: …",
    list_documents:      "List all my documents",
    compare_documents:   "Compare these two documents: …",
    read_document:       "Read and explain this document: …",
    search_text:         "Find text in my documents: …",
    generate_hypothesis: "Generate a hypothesis about…",
    search_literature:   "Search the literature for…",
};

// Icon per tool
const TOOL_ICONS = {
    search_papers:       "🔍",
    summarise_notes:     "📝",
    create_note:         "✏️",
    list_documents:      "📂",
    compare_documents:   "⚖️",
    read_document:       "📖",
    search_text:         "🔎",
    generate_hypothesis: "💡",
    search_literature:   "🌐",
};

function renderTools(tools) {
    if (!tools || tools.length === 0) {
        toolListEl.innerHTML = '<div class="doc-empty">No tools available.</div>';
        return;
    }

    const local = tools.filter(t => t.source !== "cloud");
    const cloud = tools.filter(t => t.source === "cloud");

    function renderGroup(group) {
        return group.map(t => {
            const icon   = TOOL_ICONS[t.name] || "🔧";
            const prompt = TOOL_PROMPTS[t.name] || t.name.replace(/_/g, " ");
            const badge  = toolSourceBadge(t.source);
            // Insert prompt into notebook chat input if available, else ignore
            return `
                <div class="tool-item" title="${escapeHtml(t.description || "")}"
                     onclick="(()=>{const q=document.getElementById('nb-query-input');if(q){q.value=${JSON.stringify(prompt)};q.focus();q.style.height='auto';q.style.height=Math.min(q.scrollHeight,120)+'px';}})()">
                    <span class="tool-icon">${icon}</span>
                    <div class="tool-text">
                        <div class="tool-name">${badge} ${escapeHtml(t.name.replace(/_/g, " "))}</div>
                        <div class="tool-desc">${escapeHtml(t.description || "")}</div>
                    </div>
                </div>`;
        }).join("");
    }

    let html = "";
    if (local.length) {
        html += `<div class="tool-group-label">🔒 On-device</div>${renderGroup(local)}`;
    }
    if (cloud.length) {
        html += `<div class="tool-group-label">☁️ Cloud</div>${renderGroup(cloud)}`;
    }
    toolListEl.innerHTML = html;
}

// ── Tool panel collapse toggle ──────────────────────────────────────
const toolPanelToggle = document.getElementById("tool-panel-toggle");
if (toolPanelToggle) {
    toolPanelToggle.addEventListener("click", () => {
        toolPanelToggle.classList.toggle("collapsed");
        toolListEl.classList.toggle("collapsed");
    });
}

// ── Researcher Quick Links collapse toggle ──────────────────────────
const resToolsToggle = document.getElementById("res-tools-toggle");
if (resToolsToggle) {
    const resToolGrid = document.getElementById("res-tool-grid");
    resToolsToggle.addEventListener("click", () => {
        resToolsToggle.classList.toggle("collapsed");
        if (resToolGrid) resToolGrid.classList.toggle("collapsed");
    });
}

// ── DOI / arXiv quick resolver ───────────────────────────────────────
document.getElementById("doi-go-btn")?.addEventListener("click", () => {
    const raw = (document.getElementById("doi-input")?.value || "").trim();
    if (!raw) return;
    let url;
    // arXiv: 1234.56789 or arxiv:1234.56789 or https://arxiv.org/abs/...
    if (/^arxiv:/i.test(raw)) {
        url = `https://arxiv.org/abs/${raw.replace(/^arxiv:/i, "")}`;
    } else if (/^\d{4}\.\d+/.test(raw)) {
        url = `https://arxiv.org/abs/${raw}`;
    } else if (/^https?:\/\//.test(raw)) {
        url = raw;
    } else {
        // Assume DOI
        const doi = raw.replace(/^doi:\s*/i, "");
        url = `https://doi.org/${doi}`;
    }
    window.open(url, "_blank");
    if (document.getElementById("doi-input")) document.getElementById("doi-input").value = "";
});
document.getElementById("doi-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("doi-go-btn")?.click();
});

// ── Quick research prompts (shown in welcome area & used by notebook chat) ──

let allDocuments = []; // populated if tools list has doc info

function loadQuickPrompts() {
    if (!quickButtonsEl) return;

    const prompts = [
        {
            title: "Summary with citations",
            hint: "Scan sources and cite filenames",
            text: "Summarize the notebook sources with inline citations [filename] and highlight gaps to investigate next.",
            icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>',
        },
        {
            title: "Compare documents",
            hint: "Contrast two source files",
            text: "Compare the main themes across my notebook sources — output key differences with citations.",
            icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
        },
        {
            title: "Design experiment",
            hint: "Propose next steps and metrics",
            text: "Based on the notebook sources, propose the next experiment to pursue; include materials, protocol steps, and measurement plan with citations.",
            icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3v11"/><path d="M15 3v4"/><path d="M9 14l-4 7h14l-4-7"/><circle cx="9" cy="14" r="2"/></svg>',
        },
        {
            title: "Literature + local",
            hint: "Blend sources with web search",
            text: "Search recent literature on the topics in my notebook and combine with local findings; cite online papers as [L1], [L2] and local files by name.",
            icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
        }
    ];

    const html = prompts.map(p => `
        <button class="quick-btn" data-text="${escapeHtml(p.text)}">
            <strong><span class="quick-icon">${p.icon}</span> ${escapeHtml(p.title)}</strong>
            <span>${escapeHtml(p.hint)}</span>
        </button>
    `).join("");

    function bindQuickButtons(container) {
        if (!container) return;
        container.innerHTML = html;
        container.querySelectorAll(".quick-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                if (!currentConvId) {
                    showToast("Open a conversation first to use quick prompts", "info");
                    return;
                }
                nbQueryInput.value = btn.dataset.text;
                nbQueryInput.focus();
                nbQueryInput.dispatchEvent(new Event("input"));
            });
        });
    }

    bindQuickButtons(quickButtonsEl);
    bindQuickButtons(welcomeQuickButtonsEl);
}

// ── Keyboard shortcuts ──────────────────────────────────────────────

document.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;

    // Cmd/Ctrl+K → focus doc search
    if (mod && e.key === "k") {
        if (docSearchInput) {
            e.preventDefault();
            docSearchInput.focus();
            if (window.innerWidth <= 768 && !sidebar.classList.contains("open")) {
                toggleSidebar();
            }
        }
        return;
    }

    if (e.key === "Escape") {
        if (previewOverlay.classList.contains("open")) {
            closePreview();
        } else if (privacyLogOverlay.classList.contains("open")) {
            privacyLogOverlay.classList.remove("open");
        } else if (document.getElementById("nb-create-overlay")?.classList.contains("open")) {
            closeNbCreateModal();
        } else if (docSearchInput && document.activeElement === docSearchInput) {
            docSearchInput.value = "";
            docSearchInput.dispatchEvent(new Event("input"));
            docSearchInput.blur();
        } else if (document.activeElement === nbQueryInput) {
            nbQueryInput.blur();
        }
        return;
    }

    // Cmd/Ctrl+Enter → send message in notebook chat
    if (mod && e.key === "Enter") {
        e.preventDefault();
        if (currentConvId) nbSendMessage(nbQueryInput.value);
        return;
    }

    // "/" → focus notebook chat input (when not already in an input)
    if (e.key === "/" && !mod && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
        if (currentConvId) {
            e.preventDefault();
            nbQueryInput.focus();
        }
    }
});

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
            previewPdf.src = _previewPdfName; // already a full URL for notebook sources
        }
    } else {
        previewBody.style.display = "";
        previewPdf.style.display = "none";
        previewTabText.classList.add("active");
        previewTabPdf.classList.remove("active");
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
const sidebarCollapseBtn = document.getElementById("sidebar-collapse-btn");

function setSidebarCollapsed(collapsed) {
    if (!sidebar) return;
    sidebar.classList.toggle("collapsed", collapsed);
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    if (collapsed) {
        sidebar.classList.remove("open");
        sidebarOverlay?.classList.remove("open");
    }
}

function toggleSidebar() {
    if (sidebar?.classList.contains("collapsed")) {
        setSidebarCollapsed(false);
        return;
    }
    sidebar.classList.toggle("open");
    sidebarOverlay.classList.toggle("open");
}

menuBtn.addEventListener("click", toggleSidebar);
sidebarOverlay.addEventListener("click", toggleSidebar);
sidebarCollapseBtn?.addEventListener("click", () => {
    setSidebarCollapsed(!sidebar.classList.contains("collapsed"));
});

// ── Init (moved below notebook state declarations — see bottom of NOTEBOOK STATE) ──

// ════════════════════════════════════════════════════════════════════════
//  NOTEBOOK STATE
// ════════════════════════════════════════════════════════════════════════

let currentNbId   = null;
let currentConvId = null;
let nbBusy        = false;
let nbSseCtrl     = null;   // AbortController for active SSE stream
let _allNotebooks = [];     // Cached for search/filter

const nbListEl        = document.getElementById("nb-list");
const nbNameDisplay   = document.getElementById("nb-name-display");
const nbSourcesWrap   = document.getElementById("nb-sources-wrap");
const nbConvWrap      = document.getElementById("nb-conv-wrap");
const nbSourceList    = document.getElementById("nb-source-list");
const nbConvList      = document.getElementById("nb-conv-list");
const nbPlaceholder   = document.getElementById("nb-placeholder");
const nbTabsWrap      = document.getElementById("nb-tabs-wrap");
const nbPanelFiles    = document.getElementById("nb-panel-files");
const nbPanelChats    = document.getElementById("nb-panel-chats");
const nbTabFiles      = document.getElementById("nb-tab-files");
const nbTabChats      = document.getElementById("nb-tab-chats");
const nbChatWrap      = document.getElementById("nb-chat-wrap");
const nbMessagesEl    = document.getElementById("nb-messages");
const nbQueryInput    = document.getElementById("nb-query-input");
const nbSendBtn       = document.getElementById("nb-send-btn");
const nbClearBtn      = document.getElementById("nb-clear-btn");
const nbCitationsBar  = document.getElementById("nb-citations-bar");
const nbCitationsList = document.getElementById("nb-citations-list");

// Ensure clicks on dynamically-rendered "Create your first notebook" buttons always open
// the create-notebook modal (delegation prevents missed listeners when the list is re-rendered).
nbListEl?.addEventListener('click', (e) => {
    const btn = e.target && e.target.closest && e.target.closest('[data-action="new-notebook"]');
    if (btn) {
        openNbCreateModal();
    }
});
const nbVoiceBtn      = document.getElementById("nb-voice-btn");
const chatWelcome     = document.getElementById("chat-welcome");

// Main-area tabs & panels
const mainTabsBar      = document.getElementById("main-tabs");
const mainTabChat      = document.getElementById("main-tab-chat");
const mainTabWrite     = document.getElementById("main-tab-write");
const mainTabCal       = document.getElementById("main-tab-cal");
const mainTabCode      = document.getElementById("main-tab-code");
const mainPanelChat    = document.getElementById("main-panel-chat");
const mainPanelWrite   = document.getElementById("main-panel-write");
const mainPanelCal     = document.getElementById("main-panel-cal");
const mainPanelCode    = document.getElementById("main-panel-code");
const mainTabNbName    = document.getElementById("main-tab-nb-name");

// ── Notebook sidebar tab switching (Files | Chats only) ──────────────
function switchNbTab(tab) {
    [nbTabFiles, nbTabChats].forEach(t => t && t.classList.remove("active"));
    [nbPanelFiles, nbPanelChats].forEach(p => p && (p.style.display = "none"));
    if (tab === "files") {
        nbTabFiles?.classList.add("active");
        if (nbPanelFiles) nbPanelFiles.style.display = "";
    } else if (tab === "chats") {
        nbTabChats?.classList.add("active");
        if (nbPanelChats) nbPanelChats.style.display = "";
    }
}
nbTabFiles?.addEventListener("click", () => switchNbTab("files"));
nbTabChats?.addEventListener("click", () => switchNbTab("chats"));

// ── Main-area tab switching (Chat | Write | Calendar | Code) ─────────
function switchMainTab(tab) {
    [mainTabChat, mainTabWrite, mainTabCal, mainTabCode].forEach(t => t && t.classList.remove("active"));
    [mainPanelChat, mainPanelWrite, mainPanelCal, mainPanelCode].forEach(p => p && (p.style.display = "none"));
    if (tab === "chat") {
        mainTabChat?.classList.add("active");
        if (mainPanelChat) mainPanelChat.style.display = "";
    } else if (tab === "write") {
        mainTabWrite?.classList.add("active");
        if (mainPanelWrite) mainPanelWrite.style.display = "";
        loadPaper(currentNbId);
    } else if (tab === "cal") {
        mainTabCal?.classList.add("active");
        if (mainPanelCal) mainPanelCal.style.display = "";
        loadCalendar(currentNbId);
    } else if (tab === "code") {
        mainTabCode?.classList.add("active");
        if (mainPanelCode) mainPanelCode.style.display = "";
        if (typeof codeIDE !== 'undefined') codeIDE.refresh();
    }
}
mainTabChat?.addEventListener("click", () => switchMainTab("chat"));
mainTabWrite?.addEventListener("click", () => switchMainTab("write"));
mainTabCal?.addEventListener("click", () => switchMainTab("cal"));
mainTabCode?.addEventListener("click", () => switchMainTab("code"));

function enterNotebookChat(nbName, convTitle) {
    if (chatWelcome) chatWelcome.style.display = "none";
    nbChatWrap.style.display = "";
    document.getElementById("nb-chat-title").textContent = nbName + " \u2014 " + convTitle;
}

function exitNotebookChat() {
    nbChatWrap.style.display = "none";
    if (chatWelcome) chatWelcome.style.display = "";
    currentConvId = null;
    nbMessagesEl.innerHTML = "";
    nbCitationsBar.style.display = "none";
    nbConvList.querySelectorAll(".nb-conv-item").forEach(el => el.classList.remove("active"));
}

document.getElementById("nb-chat-back").addEventListener("click", exitNotebookChat);

// ── Notebook list ────────────────────────────────────────────────────

async function loadNotebooks() {
    // Show loading shimmer while fetching
    if (nbListEl) {
        nbListEl.innerHTML = '<div class="nb-loading"></div><div class="nb-loading" style="width:80%"></div><div class="nb-loading" style="width:60%"></div>';
    }
    try {
        const res = await fetch("/api/notebooks");
        const data = await res.json();
        renderNotebookList(data.notebooks || []);
    } catch (err) {
        if (nbListEl) nbListEl.innerHTML = '<div class="doc-empty" style="font-size:12px;padding:12px 8px;color:var(--red)">Failed to load notebooks.</div>';
        showToast("Failed to load notebooks", "error");
    }
}

function renderNotebookList(notebooks) {
    _allNotebooks = notebooks || [];
    _renderNbItems(_allNotebooks);
}

async function selectNotebook(nbId, name) {
    currentNbId = nbId;
    if (currentConvId) exitNotebookChat();
    currentConvId = null;

    // Reset writing copilot conversation for new notebook
    if (typeof _copilotConvId !== 'undefined') _copilotConvId = null;

    nbNameDisplay.textContent = name;
    nbTabsWrap.style.display   = "";
    nbPlaceholder.style.display = "none";

    // Show main-area tab bar when notebook is selected
    if (mainTabsBar) mainTabsBar.style.display = "";
    if (mainTabNbName) mainTabNbName.textContent = name;

    // always start on Files tab in sidebar and Chat tab in main
    switchNbTab("files");
    switchMainTab("chat");

    nbListEl.querySelectorAll(".nb-item").forEach(el => {
        el.classList.toggle("active", el.dataset.id === nbId);
    });

    await Promise.all([loadSources(nbId), loadConversations(nbId)]);
}

// ── Notebook creation modal ───────────────────────────────────────────
// Look up elements fresh each time to avoid stale / null references.
function _nbModal(id) { return document.getElementById(id); }

function openNbCreateModal() {
    const overlay = _nbModal("nb-create-overlay");
    const nameEl  = _nbModal("nb-create-name");
    const descEl  = _nbModal("nb-create-desc");
    if (!overlay || !nameEl) { console.warn("nb-create modal elements missing"); return; }
    nameEl.value = "";
    if (descEl) descEl.value = "";
    overlay.classList.add("open");
    setTimeout(() => nameEl.focus(), 50);
}

function closeNbCreateModal() {
    const overlay = _nbModal("nb-create-overlay");
    if (overlay) overlay.classList.remove("open");
}

async function submitNewNotebook() {
    const nameEl   = _nbModal("nb-create-name");
    const submitEl = _nbModal("nb-create-submit");
    if (!nameEl || !submitEl) return;
    const name = nameEl.value.trim();
    if (!name) { nameEl.focus(); return; }
    submitEl.disabled = true;
    submitEl.textContent = "Creating…";

    /* ── Step 1: create on server ─────────────────────────────── */
    let data;
    try {
        const res = await fetch("/api/notebooks", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
        });
        if (!res.ok) {
            const errBody = await res.text().catch(() => "");
            console.error("Create notebook failed:", res.status, errBody);
            showToast(`Failed to create notebook (${res.status})`, "error");
            return;                     // stop here — nothing was created
        }
        data = await res.json();
    } catch (err) {
        console.error("Create notebook network error:", err);
        showToast("Cannot reach server — is it running?", "error");
        return;
    } finally {
        submitEl.disabled = false;
        submitEl.textContent = "Create notebook";
    }

    /* ── Step 2: close modal & refresh UI (non-critical) ──────── */
    closeNbCreateModal();
    showToast(`Notebook "${name}" created`, "success");
    try {
        await loadNotebooks();
        if (data?.id) selectNotebook(data.id, data.name || name);
    } catch (uiErr) {
        console.warn("Notebook created but UI refresh failed:", uiErr);
    }
}

// Bind modal event listeners (safe with ?.)
document.getElementById("nb-new-btn")?.addEventListener("click", openNbCreateModal);
document.getElementById("nb-create-close")?.addEventListener("click", closeNbCreateModal);
document.getElementById("nb-create-overlay")?.addEventListener("click", (e) => { if (e.target === e.currentTarget) closeNbCreateModal(); });
document.getElementById("nb-create-submit")?.addEventListener("click", submitNewNotebook);
document.getElementById("nb-create-name")?.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submitNewNotebook(); } });

// ── Init (runs AFTER all DOM refs are captured) ─────────────────────
loadTools();
loadNotebooks();
loadQuickPrompts();

async function deleteNotebook(nbId) {
    if (!confirm("Delete this notebook and all its sources?")) return;
    await fetch(`/api/notebooks/${nbId}`, { method: "DELETE" });
    showToast("Notebook deleted", "info");
    if (currentNbId === nbId) {
        if (currentConvId) exitNotebookChat();
        currentNbId = null;
        nbNameDisplay.textContent = "Notebooks";
        nbTabsWrap.style.display   = "none";
        nbPlaceholder.style.display = "";
        // Hide main-area tabs and reset to chat
        if (mainTabsBar) mainTabsBar.style.display = "none";
        switchMainTab("chat");
    }
    loadNotebooks();
}

// ── Sources ──────────────────────────────────────────────────────────

let _srcPollTimers = {};

let _currentSources = [];

async function loadSources(nbId) {
    try {
        const res  = await fetch(`/api/notebooks/${nbId}/sources`);
        const data = await res.json();
        _currentSources = data.sources || [];
        renderSources(nbId, _currentSources);
    } catch {
        _currentSources = [];
        showToast("Failed to load sources", "error");
    }
}

function srcTypeIcon(s) {
    const name = (s.filename || s.url || "").toLowerCase();
    if (name.endsWith(".pdf"))
        return `<svg class="src-type-icon pdf" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
    if (name.endsWith(".md"))
        return `<svg class="src-type-icon md" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/></svg>`;
    if (s.url)
        return `<svg class="src-type-icon url" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`;
    return `<svg class="src-type-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/></svg>`;
}

function renderSources(nbId, sources) {
    if (!sources.length) {
        nbSourceList.innerHTML = '<div class="doc-empty" style="font-size:12px;padding:12px 8px">No sources yet — add a file or URL above.</div>';
        return;
    }
    nbSourceList.innerHTML = sources.map(s => {
        const label = escapeHtml(s.title || s.filename || s.url || "Source");
        const canPreview = s.filename || s.url;
        // "locked" = on-device only, never sent to cloud
        const isLocked = s.sensitivity === "confidential" || s.sensitivity === "locked";
        const lockBtn = `<button class="src-lock-btn${isLocked ? " locked" : ""}" data-src-id="${s.id}" data-nb-id="${nbId}" data-locked="${isLocked}" title="${isLocked ? "Locked — on-device only, click to unlock" : "Unlocked — click to lock from cloud"}">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="11" width="18" height="11" rx="2"/>
                ${isLocked
                    ? '<path d="M7 11V7a5 5 0 0 1 10 0v4"/>'
                    : '<path d="M7 11V7a5 5 0 0 1 5-5 5 5 0 0 1 5 5v4"/>'}
            </svg>
        </button>`;
        const rawHref = s.filename ? `/api/notebooks/${nbId}/sources/${s.id}/raw` : (s.url || "#");
        return `
        <div class="nb-source-item${canPreview ? " nb-source-clickable" : ""}${isLocked ? " src-item-locked" : ""}" data-src-id="${s.id}" data-nb-id="${nbId}" data-filename="${escapeHtml(s.filename || "")}" data-url="${escapeHtml(s.url || "")}" data-title="${label}" title="${label}">
            <span class="src-dot ${s.status}" title="${s.status}"></span>
            ${srcTypeIcon(s)}
            <span class="src-name">${label}</span>
            ${isLocked ? '<span class="src-locked-badge" title="On-device only — excluded from cloud"><svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M18 10h-1V7a5 5 0 0 0-10 0v3H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2z"/></svg></span>' : ""}
            <div class="src-actions">
                ${lockBtn}
                ${canPreview ? `<button class="src-preview-btn" data-src-id="${s.id}" title="Preview">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>` : ""}
                <a class="src-download-btn" href="${rawHref}" download title="Download" ${s.filename ? "" : 'target="_blank" rel="noopener"'}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                </a>
                <button class="src-del" data-id="${s.id}" title="Remove">×</button>
            </div>
        </div>`;
    }).join("");

    // preview on click (whole row)
    nbSourceList.querySelectorAll(".nb-source-clickable").forEach(el => {
        el.addEventListener("click", (e) => {
            if (e.target.closest(".src-del, .src-lock-btn, .src-download-btn")) return;
            previewNbSource(el);
        });
    });

    nbSourceList.querySelectorAll(".src-del").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            deleteSource(nbId, btn.dataset.id);
        });
    });

    // lock / unlock toggle
    nbSourceList.querySelectorAll(".src-lock-btn").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const nowLocked = btn.dataset.locked === "true";
            const newLevel  = nowLocked ? "shareable" : "confidential";
            await fetch(`/api/notebooks/${btn.dataset.nbId}/sources/${btn.dataset.srcId}/sensitivity`, {
                method:  "PUT",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ level: newLevel }),
            });
            loadSources(nbId);
        });
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

async function previewNbSource(el) {
    const filename = el.dataset.filename;
    const url      = el.dataset.url;
    const title    = el.dataset.title || filename || url;
    const nbId     = el.dataset.nbId;
    const srcId    = el.dataset.srcId;

    previewTitle.textContent = title;
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
        const res = await fetch(`/api/notebooks/${nbId}/sources/${srcId}/preview`);
        if (res.ok) {
            const data = await res.json();
            previewBody.textContent = data.preview || "(no preview)";
            previewTitle.textContent = `${title} (${data.size_kb ?? "?"} KB)`;
            if (data.has_pdf) {
                _previewHasPdf = true;
                _previewPdfName = `/api/notebooks/${nbId}/sources/${srcId}/raw`;
                previewTabPdf.style.display = "";
                previewDownload.href = _previewPdfName;
                previewDownload.download = filename || "file.pdf";
                showPreviewTab("pdf");
            } else {
                previewDownload.href = filename ? `/api/notebooks/${nbId}/sources/${srcId}/raw` : (url || "#");
                previewDownload.download = filename || "";
            }
            return;
        }
        previewBody.textContent = url
            ? `URL source: ${url}\n\nPreview not available.`
            : "Preview not available.";
    } catch (err) {
        previewBody.textContent = `Failed to load: ${err.message}`;
    }
}

async function deleteSource(nbId, srcId) {
    await fetch(`/api/notebooks/${nbId}/sources/${srcId}`, { method: "DELETE" });
    loadSources(nbId);
}

// ── LaTeX editor (main area Write panel) ─────────────────────────────

const latexSource     = document.getElementById("latex-source-main");
const latexPreview    = document.getElementById("latex-preview-main");
const paperSaveBtn    = document.getElementById("paper-save-btn-main");
const paperSaveStatus = document.getElementById("paper-save-status-main");
const paperWordCount  = document.getElementById("paper-word-count-main");
const paperExportBtn  = document.getElementById("paper-export-btn-main");
const lineNumbersEl   = document.getElementById("write-line-numbers");

// ── Line numbers ──────────────────────────────────────────────────────
let _prevLineCount = 0;

function updateLineNumbers() {
    if (!latexSource || !lineNumbersEl) return;
    const lines = latexSource.value.split('\n').length;
    if (lines === _prevLineCount) return;
    _prevLineCount = lines;
    let html = '';
    for (let i = 1; i <= lines; i++) {
        html += `<span class="ln">${i}</span>`;
    }
    lineNumbersEl.innerHTML = html;
}

function highlightActiveLine() {
    if (!latexSource || !lineNumbersEl) return;
    const pos = latexSource.selectionStart;
    const lineNum = latexSource.value.substring(0, pos).split('\n').length;
    const lns = lineNumbersEl.querySelectorAll('.ln');
    lns.forEach((el, idx) => {
        el.classList.toggle('active', idx + 1 === lineNum);
    });
}

// Sync line numbers scroll with textarea scroll
function syncLineNumberScroll() {
    if (!latexSource || !lineNumbersEl) return;
    lineNumbersEl.scrollTop = latexSource.scrollTop;
}

if (latexSource) {
    latexSource.addEventListener('scroll', syncLineNumberScroll);
    latexSource.addEventListener('input', () => { updateLineNumbers(); highlightActiveLine(); });
    latexSource.addEventListener('click', highlightActiveLine);
    latexSource.addEventListener('keyup', highlightActiveLine);
    // Initial render
    setTimeout(() => { updateLineNumbers(); highlightActiveLine(); }, 50);
}

// ── Resizable panes ──────────────────────────────────────────────────
function initResize(handleId, leftSelector, rightSelector, parentSelector) {
    const handle = document.getElementById(handleId);
    if (!handle) return;
    const parent = handle.closest(parentSelector) || handle.parentElement;

    let isResizing = false, startX = 0, startLeftW = 0, parentW = 0;

    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        const leftEl = parent.querySelector(leftSelector) || document.getElementById(leftSelector.replace('#',''));
        const rightEl = parent.querySelector(rightSelector) || document.getElementById(rightSelector.replace('#',''));
        if (!leftEl || !rightEl) return;
        isResizing = true;
        startX = e.clientX;
        startLeftW = leftEl.getBoundingClientRect().width;
        // Use the parent total width minus the handle for accurate calculation
        const handleW = handle.getBoundingClientRect().width;
        parentW = parent.getBoundingClientRect().width - handleW;
        handle.classList.add('active');
        document.body.classList.add('resizing');

        function onMouseMove(ev) {
            if (!isResizing) return;
            const dx = ev.clientX - startX;
            const minW = 120;
            let newLeftW = Math.max(minW, Math.min(parentW - minW, startLeftW + dx));
            let newRightW = parentW - newLeftW;
            // Use flex-basis percentage for more reliable layout
            const leftPct = (newLeftW / parentW) * 100;
            const rightPct = (newRightW / parentW) * 100;
            leftEl.style.flex = `0 0 ${leftPct}%`;
            rightEl.style.flex = `0 0 ${rightPct}%`;
        }

        function onMouseUp() {
            isResizing = false;
            handle.classList.remove('active');
            document.body.classList.remove('resizing');
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        }

        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    });
}

// Resize handle between editor and preview (horizontal split inside .write-split)
initResize('write-resize-h1', '.write-editor-pane', '.write-preview-pane', '.write-split');

// Resize handle between editor area and copilot (vertical split inside .write-main-container)
initResize('write-resize-h2', '.write-editor-area', '.write-copilot', '.write-main-container');

let _paperSaveTimer = null;

// ── Light LaTeX → HTML renderer ──────────────────────────────────────
function latexToHtml(src) {
    if (!src || !src.trim()) {
        return '<div class="latex-placeholder">Your LaTeX preview will appear here…</div>';
    }

    // Strip preamble (everything before \begin{document})
    let body = src;
    const bodyMatch = src.match(/\\begin\{document\}([\s\S]*?)\\end\{document\}/);
    if (bodyMatch) body = bodyMatch[1];

    // Protect math zones from further processing
    const mathSlots = [];
    function protectMath(s) {
        return s
            .replace(/\\\[([\s\S]*?)\\\]/g, (_, m) => { mathSlots.push('\\[' + m + '\\]'); return `\x00MATH${mathSlots.length-1}\x00`; })
            .replace(/\$\$([\s\S]*?)\$\$/g, (_, m) => { mathSlots.push('$$' + m + '$$'); return `\x00MATH${mathSlots.length-1}\x00`; })
            .replace(/\\\(([\s\S]*?)\\\)/g, (_, m) => { mathSlots.push('\\(' + m + '\\)'); return `\x00MATH${mathSlots.length-1}\x00`; })
            .replace(/\$([^\$\n]+?)\$/g,   (_, m) => { mathSlots.push('$' + m + '$'); return `\x00MATH${mathSlots.length-1}\x00`; });
    }
    function restoreMath(s) {
        return s.replace(/\x00MATH(\d+)\x00/g, (_, i) => mathSlots[+i]);
    }

    body = protectMath(body);

    // Abstract
    body = body.replace(/\\begin\{abstract\}([\s\S]*?)\\end\{abstract\}/g,
        (_, c) => `<div class="latex-abstract">${c.trim()}</div>`);

    // Environments: equation / align / figure → pass through for MathJax
    body = body.replace(/\\begin\{(equation\*?|align\*?|gather\*?|multline\*?)\}([\s\S]*?)\\end\{\1\}/g,
        (_, env, c) => `\\begin{${env}}${c}\\end{${env}}`);
    body = body.replace(/\\begin\{figure\}[\s\S]*?\\end\{figure\}/g, '[figure]');
    body = body.replace(/\\begin\{table\}[\s\S]*?\\end\{table\}/g, '[table]');

    // itemize / enumerate
    body = body.replace(/\\begin\{itemize\}([\s\S]*?)\\end\{itemize\}/g,
        (_, c) => '<ul>' + c.replace(/\\item\s+/g, '<li>').replace(/<li>([\s\S]*?)(?=<li>|$)/g, '<li>$1</li>') + '</ul>');
    body = body.replace(/\\begin\{enumerate\}([\s\S]*?)\\end\{enumerate\}/g,
        (_, c) => '<ol>' + c.replace(/\\item\s+/g, '<li>').replace(/<li>([\s\S]*?)(?=<li>|$)/g, '<li>$1</li>') + '</ol>');

    // Sections
    body = body.replace(/\\part\{([^}]*)\}/g,       '<h1>$1</h1>');
    body = body.replace(/\\chapter\{([^}]*)\}/g,    '<h1>$1</h1>');
    body = body.replace(/\\section\{([^}]*)\}/g,    '<h2>$1</h2>');
    body = body.replace(/\\subsection\{([^}]*)\}/g, '<h3>$1</h3>');
    body = body.replace(/\\subsubsection\{([^}]*)\}/g, '<h4>$1</h4>');
    body = body.replace(/\\paragraph\{([^}]*)\}/g, '<strong>$1</strong> ');

    // Title / author / date
    body = body.replace(/\\title\{([^}]*)\}/g,  '<h1 style="text-align:center">$1</h1>');
    body = body.replace(/\\author\{([^}]*)\}/g, '<p style="text-align:center;font-style:italic">$1</p>');
    body = body.replace(/\\date\{([^}]*)\}/g,   '<p style="text-align:center;color:var(--text-tertiary)">$1</p>');
    body = body.replace(/\\maketitle/g, '');

    // Inline formatting
    body = body.replace(/\\textbf\{([^}]*)\}/g,   '<strong>$1</strong>');
    body = body.replace(/\\textit\{([^}]*)\}/g,   '<em>$1</em>');
    body = body.replace(/\\emph\{([^}]*)\}/g,     '<em class="latex-emph">$1</em>');
    body = body.replace(/\\texttt\{([^}]*)\}/g,   '<code>$1</code>');
    body = body.replace(/\\underline\{([^}]*)\}/g,'<u>$1</u>');
    body = body.replace(/\\footnote\{([^}]*)\}/g, '<sup title="$1">[fn]</sup>');
    body = body.replace(/\\cite\{([^}]*)\}/g,     '<sup>[<em>$1</em>]</sup>');
    body = body.replace(/\\ref\{([^}]*)\}/g,      '[ref:$1]');
    body = body.replace(/\\label\{([^}]*)\}/g,    '<span class="latex-label" data-label="$1"></span>');
    body = body.replace(/\\url\{([^}]*)\}/g,      '<a href="$1" target="_blank">$1</a>');
    body = body.replace(/\\href\{([^}]*)\}\{([^}]*)\}/g, '<a href="$1" target="_blank">$2</a>');

    // Horizontal rule
    body = body.replace(/\\(?:hrule|noindent\\rule\{[^}]*\}\{[^}]*\})/g, '<hr>');

    // Special chars
    body = body.replace(/\\ldots/g, '…');
    body = body.replace(/---/g, '—').replace(/--/g, '–');
    body = body.replace(/``/g, '\u201C').replace(/''/g, '\u201D');
    body = body.replace(/`/g,  '\u2018').replace(/'/g,  '\u2019');
    body = body.replace(/~/g,  '\u00A0');
    body = body.replace(/\\%/g, '%').replace(/\\&/g, '&amp;').replace(/\\\$/g, '&#36;');

    // Strip remaining unknown commands
    body = body.replace(/\\[a-zA-Z]+(\{[^}]*\}|\[[^\]]*\])*/g, '');
    body = body.replace(/[{}]/g, '');

    // Paragraphs: blank lines → <p>
    body = restoreMath(body);
    const paras = body.split(/\n{2,}/).map(p => p.trim()).filter(Boolean);
    const html = paras.map(p => {
        if (/^<(h[1-6]|ul|ol|div|hr|table|blockquote)/.test(p)) return p;
        return '<p>' + p.replace(/\n/g, ' ') + '</p>';
    }).join('\n');

    return html;
}

function updateWordCount() {
    if (!latexSource || !paperWordCount) return;
    const text = latexSource.value.replace(/\\[a-zA-Z]+(\{[^}]*\}|\[[^\]]*\])*/g, ' ').replace(/[{}%\\]/g, ' ');
    const words = text.trim().split(/\s+/).filter(w => w.length > 0);
    paperWordCount.textContent = words.length + ' words';
}

function renderLatexPreview() {
    if (!latexSource || !latexPreview) return;
    const html = latexToHtml(latexSource.value);
    latexPreview.innerHTML = `<div class="latex-paper-page">${html}</div>`;
    updateWordCount();
    // Trigger MathJax typeset
    if (window.MathJax && window.MathJax.typesetPromise) {
        window.MathJax.typesetPromise([latexPreview]).catch(() => {});
    }
}

const DEFAULT_LATEX_TEMPLATE = `\\documentclass[12pt]{article}
\\usepackage[utf8]{inputenc}
\\usepackage[T1]{fontenc}
\\usepackage{amsmath,amssymb}
\\usepackage{graphicx}
\\usepackage{hyperref}
\\usepackage[margin=1in]{geometry}

\\title{Your Paper Title}
\\author{Author Name}
\\date{\\today}

\\begin{document}

\\maketitle

\\begin{abstract}
Write a brief summary of your paper here. The abstract should concisely describe the problem, methodology, key results, and conclusions.
\\end{abstract}

\\section{Introduction}
Introduce your research topic, motivation, and objectives here.

\\section{Related Work}
Discuss relevant prior work and how your contribution differs.

\\section{Methodology}
Describe your approach, methods, and experimental setup.

\\section{Results}
Present your findings with figures, tables, and analysis.

\\section{Discussion}
Interpret your results and discuss implications.

\\section{Conclusion}
Summarize your contributions and suggest future work.

\\bibliographystyle{plain}
\\bibliography{references}

\\end{document}
`;

async function loadPaper(nbId) {
    if (!nbId || !latexSource) return;
    try {
        const res  = await fetch(`/api/notebooks/${nbId}/paper`);
        const data = await res.json();
        latexSource.value = data.content || DEFAULT_LATEX_TEMPLATE;
        renderLatexPreview();
        if (paperSaveStatus) paperSaveStatus.textContent = "";
        // If we loaded the default template, auto-save it
        if (!data.content) savePaper(nbId);
    } catch {
        showToast("Failed to load paper", "error");
    }
}
async function savePaper(nbId) {
    if (!nbId || !latexSource) return;
    paperSaveStatus.textContent = "Saving…";
    try {
        await fetch(`/api/notebooks/${nbId}/paper`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ content: latexSource.value }),
        });
        paperSaveStatus.textContent = "Saved ✓";
        setTimeout(() => { paperSaveStatus.textContent = ""; }, 2000);
    } catch {
        paperSaveStatus.textContent = "Save failed";
    }
}

// Snippet insertion helper — wraps selected text for formatting commands
function insertSnippet(snippet) {
    if (!latexSource) return;
    latexSource.focus();
    const start = latexSource.selectionStart;
    const end   = latexSource.selectionEnd;
    const val   = latexSource.value;
    const selected = val.slice(start, end);
    const resolved = snippet.replace(/\\n/g, '\n');

    // If there's selected text and the snippet has {}, wrap the selection
    if (selected && resolved.includes('{}')) {
        const filled = resolved.replace('{}', '{' + selected + '}');
        latexSource.value = val.slice(0, start) + filled + val.slice(end);
        // Place cursor after the closing brace
        const cursorPos = start + filled.length;
        latexSource.selectionStart = latexSource.selectionEnd = cursorPos;
    } else if (resolved.includes('{}')) {
        // No selection: insert and place cursor inside braces
        const insertPos = start + resolved.indexOf('{}') + 1;
        latexSource.value = val.slice(0, start) + resolved + val.slice(end);
        latexSource.selectionStart = latexSource.selectionEnd = insertPos;
    } else if (resolved.includes('  ') && resolved.startsWith('$')) {
        // Inline math: "$  $" — place cursor between the spaces
        latexSource.value = val.slice(0, start) + resolved + val.slice(end);
        latexSource.selectionStart = latexSource.selectionEnd = start + 2;
    } else {
        latexSource.value = val.slice(0, start) + resolved + val.slice(end);
        latexSource.selectionStart = latexSource.selectionEnd = start + resolved.length;
    }
    renderLatexPreview();
}

// Toolbar snippet buttons
document.querySelectorAll(".paper-tool-btn[data-snippet]").forEach(btn => {
    btn.addEventListener("click", () => insertSnippet(btn.dataset.snippet));
});

if (paperSaveBtn) paperSaveBtn.addEventListener("click", () => savePaper(currentNbId));

// Export .tex
if (paperExportBtn) {
    paperExportBtn.addEventListener("click", () => {
        if (!latexSource) return;
        const blob = new Blob([latexSource.value], { type: "text/plain" });
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement("a");
        a.href     = url;
        a.download = (currentNbId ? `paper_${currentNbId}` : "paper") + ".tex";
        a.click();
        URL.revokeObjectURL(url);
    });
}

// PDF: open a print window scoped to the rendered preview
const paperPdfBtn = document.getElementById("paper-pdf-btn-main");
if (paperPdfBtn) {
    paperPdfBtn.addEventListener("click", () => {
        if (!latexPreview) return;
        const previewHtml = latexPreview.innerHTML;
        const win = window.open("", "_blank", "width=900,height=700");
        win.document.write(`<!DOCTYPE html><html><head>
<meta charset="UTF-8">
<title>Paper</title>
<script>window.MathJax={tex:{inlineMath:[['$','$'],['\\\\(','\\\\)']],displayMath:[['$$','$$'],['\\\\[','\\\\]']]},svg:{fontCache:'global'},startup:{typeset:true}};<\/script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"><\/script>
<style>
  body { font-family: Georgia, 'Times New Roman', serif; font-size: 12pt; line-height: 1.7; max-width: 680px; margin: 40px auto; padding: 0 20px; color: #111; }
  h1 { font-size: 1.5em; } h2 { font-size: 1.25em; } h3 { font-size: 1.1em; }
  code { font-family: monospace; font-size: 0.88em; background: #f4f4f4; padding: 1px 4px; border-radius: 3px; }
  .latex-abstract { border-left: 3px solid #ccc; padding: 8px 12px; margin: 1em 0; font-style: italic; color: #555; }
  .latex-label, .latex-placeholder { display: none; }
  ul, ol { margin-left: 1.4em; }
  @media print { body { margin: 0; } }
</style>
</head><body>${previewHtml}<script>window.addEventListener('load',()=>{ if(window.MathJax&&window.MathJax.typesetPromise){ window.MathJax.typesetPromise().then(()=>window.print()); } else { window.print(); } });<\/script></body></html>`);
        win.document.close();
    });
}

// Live preview + auto-save on input
let _previewTimer = null;
if (latexSource) {
    latexSource.addEventListener("input", () => {
        clearTimeout(_previewTimer);
        _previewTimer = setTimeout(renderLatexPreview, 300);
        clearTimeout(_paperSaveTimer);
        _paperSaveTimer = setTimeout(() => savePaper(currentNbId), 2000);
    });
}

// File upload
document.getElementById("nb-file-input").addEventListener("change", async (e) => {
    const input = e.target;
    if (!currentNbId || !input.files || !input.files.length) {
        if (!currentNbId) showToast("Please select a notebook first", "warn");
        return;
    }
    const files = Array.from(input.files);
    input.disabled = true;
    let success = 0, failed = 0;
    for (const f of files) {
        const fd = new FormData();
        fd.append("file", f);
        try {
            const res = await fetch(`/api/notebooks/${currentNbId}/upload`, { method: "POST", body: fd });
            if (!res.ok) {
                failed += 1;
                const body = await res.text().catch(() => "");
                console.error("Upload failed for", f.name, res.status, body);
            } else {
                success += 1;
            }
        } catch (err) {
            failed += 1;
            console.error("Network error while uploading", f.name, err);
        }
    }
    input.value = "";
    input.disabled = false;
    if (success && !failed) {
        showToast(`${success} file${success > 1 ? "s" : ""} uploaded — processing…`, "success");
    } else if (success && failed) {
        showToast(`${success} uploaded, ${failed} failed — check console for details`, "warn");
    } else {
        showToast(`Failed to upload files — check server and try again`, "error");
    }
    // Reload sources immediately AND after a delay for background processing
    await loadSources(currentNbId);
    setTimeout(() => loadSources(currentNbId), 2000);
    setTimeout(() => loadSources(currentNbId), 5000);
});

// URL add
document.getElementById("nb-url-btn").addEventListener("click", async () => {
    const urlEl = document.getElementById("nb-url-input");
    const url = (urlEl?.value || "").trim();
    if (!url || !currentNbId) return;
    try {
        const res = await fetch(`/api/notebooks/${currentNbId}/add-url`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });
        if (!res.ok) {
            const body = await res.text().catch(() => "");
            console.error("Add URL failed", res.status, body);
            showToast(`Failed to add URL (${res.status})`, "error");
            return;
        }
        urlEl.value = "";
        showToast("URL source added — processing…", "success");
        setTimeout(() => loadSources(currentNbId), 700);
    } catch (err) {
        console.error("Network error adding URL", err);
        showToast("Cannot reach server — URL not added", "error");
    }
});

// ── Conversations ─────────────────────────────────────────────────────

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
    try {
        const res  = await fetch(`/api/notebooks/${nbId}/conversations`);
        const data = await res.json();
        renderConversations(nbId, data.conversations || []);
    } catch {
        showToast("Failed to load conversations", "error");
    }
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

    switchNbTab("chats");

    if (window.innerWidth <= 1200) {
        nbColumn.classList.add("closed");
        nbSidebarOverlay.classList.remove("open");
    }

    nbConvList.querySelectorAll(".nb-conv-item").forEach(el => {
        el.classList.toggle("active", el.dataset.id === convId);
    });

    try {
        const res  = await fetch(`/api/notebooks/${nbId}/conversations/${convId}/messages`);
        const data = await res.json();
        nbMessagesEl.innerHTML = "";
        const msgs = data.messages || [];
        if (msgs.length === 0) {
            nbAddMessage("assistant", "Ask a question about your sources. I'll ground my answers in the documents you've uploaded to this notebook.");
        } else {
            for (const m of msgs) {
                if (m.role === "user") {
                    nbAddMessage("user", m.content);
                } else {
                    nbAddMessage("assistant", m.content, m.citations || []);
                }
            }
        }
        nbScrollBottom();
    } catch {
        showToast("Failed to load messages", "error");
    }
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
        } catch {
            showToast("Failed to rename conversation", "error");
        }
        loadConversations(nbId);
    }

    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); commitRename(); }
        if (e.key === "Escape") { loadConversations(nbId); }
    });
    input.addEventListener("blur", commitRename);
}

function deleteConversation(nbId, convId) {
    const overlay = document.getElementById("conv-delete-overlay");
    const confirmBtn = document.getElementById("conv-delete-confirm");
    const cancelBtn = document.getElementById("conv-delete-cancel");
    const closeBtn = document.getElementById("conv-delete-close");
    if (!overlay) return;

    overlay.classList.add("open");

    function cleanup() {
        overlay.classList.remove("open");
        confirmBtn.removeEventListener("click", onConfirm);
        cancelBtn.removeEventListener("click", cleanup);
        closeBtn.removeEventListener("click", cleanup);
        overlay.removeEventListener("click", onOverlay);
    }
    function onOverlay(e) { if (e.target === overlay) cleanup(); }
    async function onConfirm() {
        cleanup();
        try {
            await fetch(`/api/notebooks/${nbId}/conversations/${convId}`, { method: "DELETE" });
        } catch {
            showToast("Failed to delete conversation", "error");
            return;
        }
        if (currentConvId === convId) exitNotebookChat();
        loadConversations(nbId);
    }

    confirmBtn.addEventListener("click", onConfirm);
    cancelBtn.addEventListener("click", cleanup);
    closeBtn.addEventListener("click", cleanup);
    overlay.addEventListener("click", onOverlay);
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

// ── Notebook chat (merged with all corpus chat features) ──────────────

async function _ensureNbConversation() {
    if (currentConvId) return currentConvId;
    if (!currentNbId) return null;
    try {
        const res = await fetch(`/api/notebooks/${currentNbId}/conversations`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: "Chat" }),
        });
        if (!res.ok) return null;
        const data = await res.json();
        currentConvId = data.id;
        try { await loadConversations(currentNbId); } catch {}
        return currentConvId;
    } catch {
        return null;
    }
}

function nbAddMessage(role, content, citations = [], meta = null) {
    const div = document.createElement("div");
    div.className = `message ${role}`;
    let rendered;
    if (role === "assistant") {
        rendered = nbRenderAnswer(content);
    } else {
        rendered = escapeHtml(content).replace(/@([\w\s\-_.]+(?:\.\w+)?)/g, '<span class="ctx-ref">@$1</span>');
    }
    let html = `<div class="bubble">${rendered}</div>`;
    html += buildMetaHtml(meta);
    div.innerHTML = html;
    nbMessagesEl.appendChild(div);
    nbScrollBottom();

    if (citations.length) renderCitationsBar(citations);
    return div;
}

function nbAddError(text, retryFn) {
    const div = document.createElement("div");
    div.className = "message assistant";
    let html = `<div class="bubble error-bubble">${escapeHtml(text)}`;
    if (retryFn) html += ` <button class="retry-btn">Retry</button>`;
    html += `</div>`;
    div.innerHTML = html;
    if (retryFn) {
        div.querySelector(".retry-btn").addEventListener("click", () => { div.remove(); retryFn(); });
    }
    nbMessagesEl.appendChild(div);
    nbScrollBottom();
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
    if (nbVoiceBtn) nbVoiceBtn.disabled = busy && !isRecording;
    nbSendBtn.classList.toggle("disabled", busy);
}

function nbScrollBottom() {
    nbMessagesEl.scrollTop = nbMessagesEl.scrollHeight;
}

// ── Notebook chat — send message (RAG-grounded via streaming) ─────────

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

        if (!res.ok) {
            bubbleEl.classList.remove("stream-cursor");
            const errText = await res.text().catch(() => "");
            bubbleEl.innerHTML = `<span style="color:var(--red)">Server error (${res.status}): ${escapeHtml(errText || "Please try again.")}</span>`;
            nbSetBusy(false);
            nbSseCtrl = null;
            return;
        }

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

// ── Notebook chat — tool query via /api/query (corpus tools) ──────────

async function nbSendToolQuery(text) {
    if (!text.trim() || nbBusy) return;

    nbAddMessage("user", text);
    nbQueryInput.value = "";
    nbQueryInput.style.height = "auto";
    nbSetBusy(true);
    addThinking(nbMessagesEl);

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
            nbAddError(`Error: ${errMsg}`, () => nbSendToolQuery(text));
            return;
        }

        const meta = {
            source: data.source, routing_ms: data.routing_ms,
            confidence: data.confidence, data_local: data.data_local,
        };
        const calls = data.function_calls || [];

        if (data.answer) {
            nbAddMessage("assistant", data.answer, [], meta);
        } else if (calls.length > 0) {
            nbAddMessage("assistant", `Called ${calls.map(fc => fc.name).join(", ")}`, [], meta);
        } else {
            nbAddMessage("assistant", "I couldn't find a relevant tool for that query. Try rephrasing?", [], meta);
        }

        nbAddToolResults(calls, data.tool_results);
    } catch (err) {
        removeThinking();
        nbAddError(`Network error: ${err.message}`, () => nbSendToolQuery(text));
    } finally {
        nbSetBusy(false);
    }
}

// ── Smart send: RAG stream for notebook-grounded questions,
//    /api/query for tool calls. Simple heuristic based on intent. ──────

function nbSmartSend(text) {
    if (!text.trim() || nbBusy) return;
    if (_ctxActive) ctxHide();
    if (!currentNbId) { showToast("Select a notebook first", "info"); return; }
    if (!currentConvId) {
        _ensureNbConversation().then(id => {
            if (!id) { showToast("Cannot create conversation. Check server.", "error"); return; }
            nbSmartSend(text);
        });
        return;
    }
    // If the text matches a known tool-trigger pattern, use /api/query
    const toolPatterns = /^(list\s+(all\s+)?(my\s+)?documents|search\s+(the\s+)?literature|generate\s+(a\s+)?hypothesis|compare\s+(these|the|two|my)\s+documents|save\s+a\s+note|search\s+text|find\s+text)/i;
    if (toolPatterns.test(text.trim())) {
        nbSendToolQuery(text);
    } else {
        // Default: use notebook-grounded RAG streaming
        nbSendMessage(text);
    }
}

// ── Voice input for notebook chat ─────────────────────────────────────

function nbToggleVoice() {
    if (isRecording) {
        nbStopRecording();
        return;
    }

    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
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
            await nbSendVoice(blob, ext);
        };

        mediaRecorder.start();
        isRecording = true;
        if (nbVoiceBtn) nbVoiceBtn.classList.add("recording");
    }).catch(err => {
        nbAddError(`Microphone access denied: ${err.message}`);
    });
}

function nbStopRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
    isRecording = false;
    if (nbVoiceBtn) nbVoiceBtn.classList.remove("recording");
}

async function nbSendVoice(blob, ext = ".webm") {
    nbSetBusy(true);
    addThinking(nbMessagesEl);
    const form = new FormData();
    form.append("audio", blob, `recording${ext}`);

    try {
        const res = await fetch("/api/voice", { method: "POST", body: form });
        const data = await res.json();
        removeThinking();

        if (!res.ok || data.error || data.detail) {
            const errMsg = data.error || data.detail || `Server error (${res.status})`;
            const hint = data.hint ? `\n${data.hint}` : "";
            nbAddError(`Voice error: ${errMsg}${hint}`);
            return;
        }

        nbAddMessage("user", `🎤 "${data.transcript}"`);
        const meta = { source: data.source, routing_ms: data.routing_ms, confidence: data.confidence };
        const calls = data.function_calls || [];

        if (data.answer) {
            nbAddMessage("assistant", data.answer, [], meta);
        } else if (calls.length > 0) {
            nbAddMessage("assistant", `Called ${calls.map(fc => fc.name).join(", ")}`, [], meta);
        } else {
            nbAddMessage("assistant", "I couldn't process that. Try again?", [], meta);
        }

        nbAddToolResults(calls, data.tool_results);
    } catch (err) {
        removeThinking();
        nbAddError(`Voice error: ${err.message}`);
    } finally {
        nbSetBusy(false);
    }
}

// ── Route prediction indicator for notebook chat ────────────────────

const nbRouteIndicator = document.getElementById("nb-route-indicator");
let _nbClassifyTimer = null;

function updateNbRouteIndicator(text) {
    if (!nbRouteIndicator) return;
    if (!text.trim()) {
        nbRouteIndicator.innerHTML = "";
        nbRouteIndicator.className = "route-indicator";
        return;
    }
    clearTimeout(_nbClassifyTimer);
    _nbClassifyTimer = setTimeout(async () => {
        try {
            const res = await fetch("/api/classify", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: text }),
            });
            const data = await res.json();
            if (data.route === "local") {
                nbRouteIndicator.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> Stays on-device';
                nbRouteIndicator.className = "route-indicator route-local";
            } else {
                nbRouteIndicator.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg> Will use Gemini cloud';
                nbRouteIndicator.className = "route-indicator route-cloud";
            }
        } catch {}
    }, 300);
}

// ── Notebook chat event listeners ─────────────────────────────────────

let _ctxActive = false;

nbSendBtn.addEventListener("click", () => nbSmartSend(nbQueryInput.value));

nbQueryInput.addEventListener("keydown", (e) => {
    if (_ctxActive) return;
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        nbSmartSend(nbQueryInput.value);
    }
});

nbQueryInput.addEventListener("input", () => {
    nbQueryInput.style.height = "auto";
    nbQueryInput.style.height = Math.min(nbQueryInput.scrollHeight, 120) + "px";
    updateNbRouteIndicator(nbQueryInput.value);
});

if (nbVoiceBtn) nbVoiceBtn.addEventListener("click", nbToggleVoice);

nbClearBtn.addEventListener("click", () => {
    nbMessagesEl.innerHTML = "";
    nbCitationsBar.style.display = "none";
});

// ── "/" context popup for adding source references ────────────────────

const ctxPopup     = document.getElementById("context-popup");
const ctxList      = document.getElementById("context-popup-list");
const ctxEmpty     = document.getElementById("context-popup-empty");
let _ctxIdx        = 0;
let _ctxFiltered   = [];
let _ctxSlashStart = -1;

function ctxShow() {
    if (!ctxPopup || !currentNbId) return;
    _ctxActive = true;
    _ctxIdx = 0;
    ctxPopup.style.display = "";
    ctxRender("");
}

function ctxHide() {
    if (!ctxPopup) return;
    _ctxActive = false;
    _ctxIdx = 0;
    _ctxSlashStart = -1;
    ctxPopup.style.display = "none";
}

function ctxRender(filter) {
    const q = filter.toLowerCase();
    _ctxFiltered = _currentSources.filter(s => {
        const name = (s.title || s.filename || s.url || "").toLowerCase();
        return name.includes(q);
    });

    if (!_ctxFiltered.length) {
        ctxList.innerHTML = "";
        ctxEmpty.style.display = "";
        ctxEmpty.textContent = _currentSources.length === 0
            ? "No sources in this notebook — upload files first"
            : "No matching sources";
        return;
    }
    ctxEmpty.style.display = "none";
    if (_ctxIdx >= _ctxFiltered.length) _ctxIdx = 0;
    ctxList.innerHTML = _ctxFiltered.map((s, i) => {
        const label = escapeHtml(s.title || s.filename || s.url || "Source");
        const fname = s.filename || "";
        const ext = fname.includes(".") ? fname.split(".").pop().toUpperCase() : (s.url ? "URL" : "");
        const icon = fname.toLowerCase().endsWith(".pdf")
            ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
            : s.url
            ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>'
            : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/></svg>';
        return `<div class="context-popup-item${i === _ctxIdx ? " active" : ""}" data-idx="${i}">
            ${icon}
            <span class="context-popup-item-name">${label}</span>
            ${ext ? `<span class="context-popup-item-type">${ext}</span>` : ""}
        </div>`;
    }).join("");

    ctxList.querySelectorAll(".context-popup-item").forEach(el => {
        el.addEventListener("mousedown", (e) => {
            e.preventDefault();
            ctxSelect(parseInt(el.dataset.idx));
        });
        el.addEventListener("mouseenter", () => {
            _ctxIdx = parseInt(el.dataset.idx);
            ctxHighlight();
        });
    });
}

function ctxHighlight() {
    ctxList.querySelectorAll(".context-popup-item").forEach((el, i) => {
        el.classList.toggle("active", i === _ctxIdx);
    });
    const activeEl = ctxList.querySelector(".context-popup-item.active");
    if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
}

function ctxSelect(idx) {
    const src = _ctxFiltered[idx];
    if (!src) { ctxHide(); return; }
    const label = src.title || src.filename || src.url || "Source";
    const val = nbQueryInput.value;
    const before = val.substring(0, _ctxSlashStart);
    const after = val.substring(nbQueryInput.selectionStart);
    nbQueryInput.value = before + "@" + label + " " + after.trimStart();
    nbQueryInput.focus();
    const cursorPos = before.length + label.length + 2;
    nbQueryInput.setSelectionRange(cursorPos, cursorPos);
    nbQueryInput.dispatchEvent(new Event("input"));
    ctxHide();
}

nbQueryInput.addEventListener("keydown", (e) => {
    if (!_ctxActive) return;
    if (e.key === "ArrowDown") {
        e.preventDefault();
        if (_ctxFiltered.length) _ctxIdx = (_ctxIdx + 1) % _ctxFiltered.length;
        ctxHighlight();
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (_ctxFiltered.length) _ctxIdx = (_ctxIdx - 1 + _ctxFiltered.length) % _ctxFiltered.length;
        ctxHighlight();
    } else if (e.key === "Enter" || e.key === "Tab") {
        if (_ctxFiltered.length) {
            e.preventDefault();
            e.stopImmediatePropagation();
            ctxSelect(_ctxIdx);
        } else {
            ctxHide();
        }
    } else if (e.key === "Escape") {
        e.preventDefault();
        ctxHide();
    }
});

nbQueryInput.addEventListener("input", () => {
    const val = nbQueryInput.value;
    const pos = nbQueryInput.selectionStart;

    if (_ctxActive) {
        const query = val.substring(_ctxSlashStart + 1, pos);
        if (_ctxSlashStart < 0 || pos <= _ctxSlashStart || query.includes("\n") || query.includes(" ") && query.length > 30) {
            ctxHide();
        } else {
            ctxRender(query);
        }
    } else if (pos > 0 && val[pos - 1] === "/") {
        const charBefore = pos > 1 ? val[pos - 2] : " ";
        if (charBefore === " " || charBefore === "\n" || pos === 1) {
            _ctxSlashStart = pos - 1;
            ctxShow();
        }
    }
});

document.addEventListener("click", (e) => {
    if (_ctxActive && !ctxPopup.contains(e.target) && e.target !== nbQueryInput) {
        ctxHide();
    }
});

// ── Mobile notebook sidebar ───────────────────────────────────────────

const nbMenuBtn        = document.getElementById("nb-menu-btn");
const nbSidebarOverlay = document.getElementById("nb-sidebar-overlay");
const nbColumn         = document.getElementById("panel-notebook");
const nbOpenBtn        = document.getElementById("nb-open-btn");

function setNbSidebarClosed(closed) {
    if (!nbColumn) return;
    nbColumn.classList.toggle("closed", closed);
    document.body.classList.toggle("nb-closed", closed);
    const isMobile = window.matchMedia("(max-width: 1200px)").matches;
    if (nbSidebarOverlay) nbSidebarOverlay.classList.toggle("open", !closed && isMobile);
    if (!closed && !currentNbId) {
        nbPlaceholder.style.display = "";
        nbTabsWrap.style.display = "none";
    }
}

function toggleNbSidebar() {
    const currentlyClosed = nbColumn?.classList.contains("closed");
    setNbSidebarClosed(!currentlyClosed);
}

nbMenuBtn?.addEventListener("click", toggleNbSidebar);
nbSidebarOverlay?.addEventListener("click", () => setNbSidebarClosed(true));
nbOpenBtn?.addEventListener("click", () => setNbSidebarClosed(false));

// ── Calendar ─────────────────────────────────────────────────────────

const EVENT_TYPE_META = {
    deadline:   { emoji: "⏰", cls: "cal-ev-deadline" },
    conference: { emoji: "🎤", cls: "cal-ev-conference" },
    meeting:    { emoji: "👥", cls: "cal-ev-meeting" },
    event:      { emoji: "📌", cls: "cal-ev-event" },
    reminder:   { emoji: "🔔", cls: "cal-ev-reminder" },
};

let _calYear  = new Date().getFullYear();
let _calMonth = new Date().getMonth(); // 0-indexed
let _calEvents = [];

const calGrid       = document.getElementById("cal-grid-main");
const calMonthLabel = document.getElementById("cal-month-label-main");
const calAddForm    = document.getElementById("cal-add-form-main");
const calEventTitle = document.getElementById("cal-event-title-main");
const calEventDate  = document.getElementById("cal-event-date-main");
const calEventType  = document.getElementById("cal-event-type-main");
const calEventNote  = document.getElementById("cal-event-note-main");

const MONTHS = ["January","February","March","April","May","June",
                "July","August","September","October","November","December"];

async function loadCalendar(nbId) {
    if (!nbId) return;
    try {
        const res = await fetch(`/api/notebooks/${nbId}/events`);
        const data = await res.json();
        _calEvents = data.events || [];
    } catch { _calEvents = []; }
    renderCalendar();
    renderUpcomingEvents();
}
function renderCalendar() {
    if (!calGrid) return;
    if (calMonthLabel) calMonthLabel.textContent = `${MONTHS[_calMonth]} ${_calYear}`;

    // Build a map: "YYYY-MM-DD" → [events]
    const byDay = {};
    for (const ev of _calEvents) {
        const d = ev.date.slice(0, 10);
        (byDay[d] = byDay[d] || []).push(ev);
    }

    // First day of month (Mon=0 offset)
    const firstDay = new Date(_calYear, _calMonth, 1);
    const totalDays = new Date(_calYear, _calMonth + 1, 0).getDate();
    let startOffset = firstDay.getDay(); // 0=Sun, need Mon=0
    startOffset = (startOffset === 0) ? 6 : startOffset - 1;

    const todayStr = new Date().toISOString().slice(0, 10);

    let html = "";
    // Empty cells before first day
    for (let i = 0; i < startOffset; i++) {
        html += `<div class="cal-day outside"></div>`;
    }
    for (let d = 1; d <= totalDays; d++) {
        const dateStr = `${_calYear}-${String(_calMonth + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
        const evs = byDay[dateStr] || [];
        const isToday = dateStr === todayStr;
        const evChips = evs.map(ev => {
            const meta = EVENT_TYPE_META[ev.type] || EVENT_TYPE_META.event;
            return `<div class="cal-day-event ${meta.cls}" data-id="${ev.id}" title="${escapeHtml(ev.title)}${ev.note ? ' — ' + escapeHtml(ev.note) : ''}">
                ${meta.emoji} ${escapeHtml(ev.title)}
                <button class="cal-chip-del" data-id="${ev.id}" title="Remove" style="float:right;border:none;background:none;cursor:pointer;font-size:12px;line-height:1;opacity:0.6">×</button>
            </div>`;
        }).join("");
        html += `<div class="cal-day${isToday ? " today" : ""}" data-date="${dateStr}">
            <span class="cal-day-num">${d}</span>
            <div class="cal-day-events">${evChips}</div>
        </div>`;
    }

    calGrid.innerHTML = html;

    // Click on day cell → pre-fill date and open form
    calGrid.querySelectorAll(".cal-day[data-date]").forEach(cell => {
        cell.addEventListener("click", (e) => {
            if (e.target.closest(".cal-chip-del")) return;
            if (calEventDate) calEventDate.value = cell.dataset.date;
            showCalForm();
        });
    });

    // Delete event chips
    calGrid.querySelectorAll(".cal-chip-del").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const eid = btn.dataset.id;
            await fetch(`/api/notebooks/${currentNbId}/events/${eid}`, { method: "DELETE" });
            await loadCalendar(currentNbId);
        });
    });
}

function showCalForm() {
    if (calAddForm) calAddForm.style.display = "";
    if (calEventTitle) calEventTitle.focus();
}
function hideCalForm() {
    if (calAddForm) calAddForm.style.display = "none";
    if (calEventTitle) calEventTitle.value = "";
    if (calEventNote)  calEventNote.value  = "";
}

// Nav buttons
document.getElementById("cal-prev-btn-main")?.addEventListener("click", () => {
    _calMonth--;
    if (_calMonth < 0) { _calMonth = 11; _calYear--; }
    renderCalendar();
});
document.getElementById("cal-next-btn-main")?.addEventListener("click", () => {
    _calMonth++;
    if (_calMonth > 11) { _calMonth = 0; _calYear++; }
    renderCalendar();
});
document.getElementById("cal-today-btn-main")?.addEventListener("click", () => {
    _calYear  = new Date().getFullYear();
    _calMonth = new Date().getMonth();
    renderCalendar();
});
document.getElementById("cal-add-btn-main")?.addEventListener("click", () => {
    const todayStr = new Date().toISOString().slice(0, 10);
    if (calEventDate) calEventDate.value = todayStr;
    showCalForm();
});
document.getElementById("cal-cancel-event-btn-main")?.addEventListener("click", hideCalForm);

document.getElementById("cal-save-event-btn-main")?.addEventListener("click", async () => {
    const title = calEventTitle?.value.trim();
    const date  = calEventDate?.value.trim();
    if (!title || !date || !currentNbId) return;
    await fetch(`/api/notebooks/${currentNbId}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            title,
            date,
            type: calEventType?.value || "event",
            note: calEventNote?.value.trim() || null,
        }),
    });
    hideCalForm();
    showToast(`Event "${title}" added`, "success");
    await loadCalendar(currentNbId);
});

// Render upcoming events list below calendar
function renderUpcomingEvents() {
    const list = document.getElementById("cal-upcoming-list-main");
    if (!list) return;
    const todayStr = new Date().toISOString().slice(0, 10);
    const upcoming = _calEvents
        .filter(ev => ev.date >= todayStr)
        .sort((a, b) => a.date.localeCompare(b.date))
        .slice(0, 10);
    if (!upcoming.length) {
        list.innerHTML = '<p style="font-size:13px;color:var(--text-secondary);padding:8px 0">No upcoming events</p>';
        return;
    }
    const meta = { deadline: { emoji: "⏰", cls: "deadline" }, conference: { emoji: "🎤", cls: "conference" }, meeting: { emoji: "👥", cls: "meeting" }, event: { emoji: "📌", cls: "event" }, reminder: { emoji: "🔔", cls: "reminder" } };
    list.innerHTML = upcoming.map(ev => {
        const m = meta[ev.type] || meta.event;
        const d = new Date(ev.date + "T00:00:00");
        const dayLabel = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
        return `<div class="cal-upcoming-item">
            <div class="cal-upcoming-date">${dayLabel}</div>
            <div class="cal-upcoming-info">
                <div class="cal-upcoming-info-title">${escapeHtml(ev.title)}</div>
                ${ev.note ? `<div class="cal-upcoming-info-note">${escapeHtml(ev.note)}</div>` : ""}
            </div>
            <span class="cal-upcoming-badge cal-day-event ${m.cls}">${m.emoji} ${ev.type}</span>
        </div>`;
    }).join("");
}

// ── AI Writing Copilot ───────────────────────────────────────────────

const copilotMessagesEl = document.getElementById("write-copilot-messages");
const copilotInput      = document.getElementById("write-copilot-input");
const copilotSendBtn    = document.getElementById("write-copilot-send");

let _copilotConvId = null; // Dedicated conversation for writing copilot

function addCopilotMessage(text, role) {
    if (!copilotMessagesEl) return;
    // Remove hint if present
    const hint = copilotMessagesEl.querySelector(".write-copilot-hint");
    if (hint) hint.remove();
    const msg = document.createElement("div");
    msg.className = `write-copilot-msg ${role}`;
    msg.textContent = text;
    copilotMessagesEl.appendChild(msg);
    copilotMessagesEl.scrollTop = copilotMessagesEl.scrollHeight;
    return msg;
}

async function _ensureCopilotConversation() {
    if (_copilotConvId) return _copilotConvId;
    try {
        const res = await fetch(`/api/notebooks/${currentNbId}/conversations`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: "Writing Copilot" }),
        });
        if (res.ok) {
            const data = await res.json();
            _copilotConvId = data.id;
            return _copilotConvId;
        }
    } catch {}
    return null;
}

async function sendCopilotMessage(prompt) {
    if (!prompt) return;
    if (!currentNbId) { showToast("Select a notebook first", "info"); return; }
    addCopilotMessage(prompt, "user");

    // Ensure we have a conversation for the copilot
    const convId = await _ensureCopilotConversation();
    if (!convId) {
        addCopilotMessage("Could not create a conversation. Is the server running?", "assistant");
        return;
    }

    // Build the actual message — prepend document context so AI is aware of current paper
    const docContent = latexSource ? latexSource.value : "";
    const fullMessage = docContent.trim()
        ? `[CONTEXT: The user is writing a LaTeX paper. Current document:\n\`\`\`latex\n${docContent.slice(0, 4000)}\n\`\`\`\n]\n\nUser request: ${prompt}`
        : prompt;

    // Add a placeholder message
    const assistantMsg = addCopilotMessage("Thinking…", "assistant");

    try {
        const res = await fetch(`/api/notebooks/${currentNbId}/conversations/${convId}/chat/stream`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: fullMessage }),
        });

        if (!res.ok) {
            assistantMsg.textContent = "Sorry, something went wrong. Try again.";
            return;
        }

        // Read streamed SSE response
        const reader = res.body?.getReader();
        if (reader) {
            const decoder = new TextDecoder();
            let fullText = "";
            assistantMsg.textContent = "";
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value, { stream: true });
                for (const line of chunk.split("\n")) {
                    if (line.startsWith("data: ")) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            if (data.token) {
                                fullText += data.token;
                                assistantMsg.textContent = fullText;
                            } else if (data.answer) {
                                fullText = data.answer;
                                assistantMsg.textContent = fullText;
                            } else if (data.text) {
                                fullText = data.text;
                                assistantMsg.textContent = fullText;
                            }
                        } catch {
                            const txt = line.slice(6).trim();
                            if (txt && txt !== "[DONE]") {
                                fullText += txt;
                                assistantMsg.textContent = fullText;
                            }
                        }
                    }
                }
                copilotMessagesEl.scrollTop = copilotMessagesEl.scrollHeight;
            }
            // If we got content, offer to insert it
            if (fullText.trim()) {
                const insertBtn = document.createElement("button");
                insertBtn.className = "write-copilot-suggest";
                insertBtn.textContent = "📝 Insert into document";
                insertBtn.style.marginTop = "8px";
                insertBtn.addEventListener("click", () => {
                    if (latexSource) {
                        const pos = latexSource.selectionStart || latexSource.value.length;
                        const before = latexSource.value.slice(0, pos);
                        const after = latexSource.value.slice(pos);
                        latexSource.value = before + "\n\n" + fullText.trim() + "\n\n" + after;
                        renderLatexPreview();
                        savePaper(currentNbId);
                        showToast("Content inserted into document", "success");
                    }
                });
                assistantMsg.appendChild(document.createElement("br"));
                assistantMsg.appendChild(insertBtn);
            }
        } else {
            const data = await res.json();
            assistantMsg.textContent = data.answer || data.text || data.response || "No response.";
        }
    } catch (err) {
        console.error("Copilot error:", err);
        assistantMsg.textContent = "Failed to get response. Check server connection.";
    }
}

// Copilot send button
copilotSendBtn?.addEventListener("click", () => {
    const text = copilotInput?.value.trim();
    if (!text) return;
    copilotInput.value = "";
    sendCopilotMessage(text);
});

// Enter to send in copilot
copilotInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        copilotSendBtn?.click();
    }
});

// Suggestion buttons in copilot
document.querySelectorAll(".write-copilot-suggest[data-prompt]").forEach(btn => {
    btn.addEventListener("click", () => {
        // Special handling for "Add citations" — fetch sources first
        if (btn.dataset.prompt.includes("citations") && currentNbId) {
            _addCitationsFromSources();
        } else {
            sendCopilotMessage(btn.dataset.prompt);
        }
    });
});

// ── Add citations from notebook sources ──────────────────────────────
async function _addCitationsFromSources() {
    if (!currentNbId || !latexSource) return;
    addCopilotMessage("Add citations from the notebook sources", "user");
    const assistantMsg = addCopilotMessage("Fetching notebook sources…", "assistant");
    try {
        const res = await fetch(`/api/notebooks/${currentNbId}/sources`);
        const data = await res.json();
        const sources = data.sources || [];
        if (!sources.length) {
            assistantMsg.textContent = "No sources found in this notebook. Upload some documents first.";
            return;
        }
        // Build bibliography entries
        let bib = "\\n% === Bibliography entries from notebook sources ===\\n";
        const citeKeys = [];
        sources.forEach((s, i) => {
            const key = (s.title || s.filename || `source${i+1}`).replace(/[^a-zA-Z0-9]/g, '').slice(0, 20).toLowerCase() + (i + 1);
            citeKeys.push(key);
            bib += `% [${key}] ${s.title || s.filename || s.url || 'Unknown'}\\n`;
        });
        const citeLine = "\\n" + citeKeys.map(k => `\\\\cite{${k}}`).join(", ") + "\\n";
        assistantMsg.textContent = `Found ${sources.length} source(s). Click below to insert citation references.`;

        const insertBtn = document.createElement("button");
        insertBtn.className = "write-copilot-suggest";
        insertBtn.textContent = "📝 Insert \\cite{} references";
        insertBtn.style.marginTop = "8px";
        insertBtn.addEventListener("click", () => {
            const pos = latexSource.selectionStart || latexSource.value.length;
            latexSource.value = latexSource.value.slice(0, pos) + citeLine + latexSource.value.slice(pos);
            renderLatexPreview();
            savePaper(currentNbId);
            showToast(`Inserted ${citeKeys.length} citation(s)`, "success");
        });
        assistantMsg.appendChild(document.createElement("br"));
        assistantMsg.appendChild(insertBtn);
    } catch (err) {
        assistantMsg.textContent = "Failed to fetch sources: " + err.message;
    }
}

// ── Tab autocompletion for LaTeX commands ─────────────────────────────
const LATEX_COMPLETIONS = [
    "\\section{}", "\\subsection{}", "\\subsubsection{}", "\\paragraph{}",
    "\\textbf{}", "\\textit{}", "\\emph{}", "\\texttt{}", "\\underline{}",
    "\\begin{equation}\n\n\\end{equation}", "\\begin{align}\n\n\\end{align}",
    "\\begin{figure}[h]\n\\centering\n\\includegraphics[width=0.8\\textwidth]{}\n\\caption{}\n\\label{fig:}\n\\end{figure}",
    "\\begin{table}[h]\n\\centering\n\\begin{tabular}{|c|c|}\n\\hline\n & \\\\\n\\hline\n\\end{tabular}\n\\caption{}\n\\label{tab:}\n\\end{table}",
    "\\begin{itemize}\n\\item \n\\end{itemize}", "\\begin{enumerate}\n\\item \n\\end{enumerate}",
    "\\cite{}", "\\ref{}", "\\label{}", "\\footnote{}",
    "\\includegraphics{}", "\\caption{}", "\\url{}",
    "\\frac{}{}", "\\sqrt{}", "\\sum_{}", "\\int_{}^{}",
    "\\alpha", "\\beta", "\\gamma", "\\delta", "\\epsilon", "\\lambda", "\\mu", "\\sigma", "\\theta", "\\omega",
    "\\infty", "\\partial", "\\nabla", "\\forall", "\\exists",
    "\\begin{abstract}\n\n\\end{abstract}", "\\maketitle",
    "\\bibliographystyle{plain}", "\\bibliography{}",
];

if (latexSource) {
    latexSource.addEventListener("keydown", (e) => {
        if (e.key !== "Tab") return;
        const pos = latexSource.selectionStart;
        const text = latexSource.value;
        // Find the word being typed (from last space/newline to cursor)
        let wordStart = pos - 1;
        while (wordStart >= 0 && !/[\s\n]/.test(text[wordStart])) wordStart--;
        wordStart++;
        const partial = text.slice(wordStart, pos);

        if (partial.startsWith("\\") && partial.length > 1) {
            e.preventDefault();
            // Find matching completions
            const matches = LATEX_COMPLETIONS.filter(c => c.startsWith(partial));
            if (matches.length === 1) {
                // Auto-complete
                const completion = matches[0];
                latexSource.value = text.slice(0, wordStart) + completion + text.slice(pos);
                const cursorOffset = completion.includes('{}') ? wordStart + completion.indexOf('{}') + 1 : wordStart + completion.length;
                latexSource.selectionStart = latexSource.selectionEnd = cursorOffset;
                renderLatexPreview();
            } else if (matches.length > 1) {
                // Find common prefix
                let common = matches[0];
                for (let i = 1; i < matches.length; i++) {
                    while (!matches[i].startsWith(common)) {
                        common = common.slice(0, -1);
                    }
                }
                if (common.length > partial.length) {
                    latexSource.value = text.slice(0, wordStart) + common + text.slice(pos);
                    latexSource.selectionStart = latexSource.selectionEnd = wordStart + common.length;
                    renderLatexPreview();
                }
                // Show a tooltip with available completions
                showToast(matches.slice(0, 8).join("  "), "info", 3000);
            }
        } else if (!partial) {
            // No prefix: insert tab as two spaces
            e.preventDefault();
            latexSource.value = text.slice(0, pos) + "  " + text.slice(pos);
            latexSource.selectionStart = latexSource.selectionEnd = pos + 2;
        }
    });
}

// Reset copilot conversation when switching notebooks
// (handled inside selectNotebook() above)

// ── Notebook search / filter ──────────────────────────────────────────

document.getElementById("nb-search")?.addEventListener("input", () => {
    const q = (document.getElementById("nb-search")?.value || "").toLowerCase().trim();
    const filtered = q ? _allNotebooks.filter(nb => (nb.name || "").toLowerCase().includes(q)) : _allNotebooks;
    _renderNbItems(filtered);
});

// Internal render that only renders the list items (used by search filter)
function _renderNbItems(notebooks) {
    if (!nbListEl) return;
    if (!notebooks.length) {
        const q = (document.getElementById("nb-search")?.value || "").trim();
        if (q) {
            nbListEl.innerHTML = '<div class="doc-empty" style="font-size:12px;padding:12px 8px">No matching notebooks.</div>';
        } else {
            nbListEl.innerHTML = `<div class="nb-empty-state">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--text-tertiary);margin-bottom:8px"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg>
                <p>No notebooks yet</p>
                <button class="nb-empty-create" data-action="new-notebook">Create your first notebook</button>
            </div>`;
        }
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
    const emptyBtn = nbListEl.querySelector('[data-action="new-notebook"]');
    if (emptyBtn) emptyBtn.addEventListener("click", openNbCreateModal);
}
