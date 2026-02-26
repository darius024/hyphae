// ══════════════════════════════════════════════════════════════════════════
//  Shared UI utilities — toast, markdown, badges, theme
// ══════════════════════════════════════════════════════════════════════════

// ── Toast notification system ───────────────────────────────────────
function showToast(message, type = "info", duration = 3000) {
    let container = document.getElementById("toast-container");
    if (!container) {
        container = document.createElement("div");
        container.id = "toast-container";
        container.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;";
        document.body.appendChild(container);
    }
    const toast = document.createElement("div");
    const colors = { success: "#34c759", error: "#ff3b30", info: "#1a5c5e", warn: "#ff9f0a" };
    toast.style.cssText = `pointer-events:auto;padding:10px 18px;border-radius:10px;background:${colors[type]||colors.info};color:#fff;font-size:13px;font-weight:500;font-family:Inter,system-ui,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,0.18);transform:translateY(12px);opacity:0;transition:all 0.25s cubic-bezier(0.16,1,0.3,1);max-width:340px;`;
    toast.textContent = message;
    container.appendChild(toast);
    requestAnimationFrame(() => { toast.style.opacity = "1"; toast.style.transform = "translateY(0)"; });
    setTimeout(() => {
        toast.style.opacity = "0"; toast.style.transform = "translateY(12px)";
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

// ── HTML escaping ───────────────────────────────────────────────────
function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// ── Markdown rendering ──────────────────────────────────────────────

function renderMarkdown(text) {
    const codeBlocks = [];
    let processed = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        const idx = codeBlocks.length;
        codeBlocks.push({ lang, code });
        return `\x00CODEBLOCK_${idx}\x00`;
    });

    const inlineCodes = [];
    processed = processed.replace(/`([^`\n]+)`/g, (_, code) => {
        const idx = inlineCodes.length;
        inlineCodes.push(code);
        return `\x00INLINE_${idx}\x00`;
    });

    let html = escapeHtml(processed);

    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    html = html.replace(/^###\s+(.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^##\s+(.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^#\s+(.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    html = html.replace(/^[-•]\s+(.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>[\s\S]*?<\/li>)(?=\s*(?:<br>)?\s*(?:<li>|$))/g, "$1");
    html = html.replace(/((?:<li>[\s\S]*?<\/li>\s*(?:<br>\s*)*)+)/g, "<ul>$1</ul>");
    html = html.replace(/<ul>([\s\S]*?)<\/ul>/g, (_, inner) => "<ul>" + inner.replace(/<br>\s*/g, "") + "</ul>");
    html = html.replace(/^\d+\.\s+(.+)$/gm, "<li>$1</li>");
    html = html.replace(/\n/g, "<br>");
    html = html.replace(/<br>\s*(<h[234]>)/g, "$1");
    html = html.replace(/(<\/h[234]>)\s*<br>/g, "$1");

    inlineCodes.forEach((code, i) => {
        html = html.replace(`\x00INLINE_${i}\x00`, `<code>${escapeHtml(code)}</code>`);
    });
    codeBlocks.forEach(({ lang, code }, i) => {
        const langLabel = lang ? `<span class="code-lang">${escapeHtml(lang)}</span>` : "";
        html = html.replace(`\x00CODEBLOCK_${i}\x00`, `<pre>${langLabel}<code>${escapeHtml(code.trim())}</code></pre>`);
    });

    return html;
}

// ── Badges & meta ───────────────────────────────────────────────────

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

// ── Tool result formatting ──────────────────────────────────────────

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

// ── Thinking indicator ──────────────────────────────────────────────

function addThinking(targetEl) {
    const div = document.createElement("div");
    div.className = "message assistant";
    div.id = "thinking";
    div.innerHTML = '<div class="thinking"><span></span><span></span><span></span></div>';
    targetEl.appendChild(div);
    return div;
}

function removeThinking() {
    const el = document.getElementById("thinking");
    if (el) el.remove();
}

// ── Dark mode ─────────────────────────────────────────────────────────

const THEME_KEY = "hyphae_theme";

function applyTheme(dark) {
    if (dark) {
        document.body.classList.add("dark");
    } else {
        document.body.classList.remove("dark");
    }
    const label = document.querySelector(".theme-label");
    if (label) label.textContent = dark ? "Light mode" : "Dark mode";
    try { localStorage.setItem(THEME_KEY, dark ? "dark" : "light"); } catch {}
}

(function initTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark") applyTheme(true);
    else if (saved === "light") applyTheme(false);
    else if (window.matchMedia("(prefers-color-scheme: dark)").matches) applyTheme(true);
})();

const _themeBtn = document.getElementById("theme-toggle");
if (_themeBtn) {
    _themeBtn.addEventListener("click", function(e) {
        e.preventDefault();
        e.stopPropagation();
        const isDark = document.body.classList.contains("dark");
        applyTheme(!isDark);
    });
}
