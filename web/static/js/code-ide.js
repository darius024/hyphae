// ══════════════════════════════════════════════════════════════════════════
//  CODE IDE — VS Code-like file browser, editor & Git integration
// ══════════════════════════════════════════════════════════════════════════
const codeIDE = (() => {
    const API = '/api/code';
    const GIT = '/api/git';

    // DOM refs — connect screen
    const connectScreen  = document.getElementById('code-connect');
    const ideEl          = document.getElementById('code-ide');
    const repoUrlInput   = document.getElementById('code-repo-url');
    const cloneBtn       = document.getElementById('code-clone-btn');
    const connectStatus  = document.getElementById('code-connect-status');
    const recentsEl      = document.getElementById('code-connect-recents');
    const repoNameEl     = document.getElementById('code-repo-name');
    const disconnectBtn  = document.getElementById('code-disconnect-btn');

    // DOM refs — IDE
    const fileTree       = document.getElementById('code-file-tree');
    const tabBar         = document.getElementById('code-tab-bar');
    const breadcrumb     = document.getElementById('code-breadcrumb');
    const editorWrap     = document.getElementById('code-editor-wrap');
    const welcomeEl      = document.getElementById('code-welcome');
    const editorContent  = document.getElementById('code-editor-content');
    const lineNumbers    = document.getElementById('code-line-numbers');
    const textarea       = document.getElementById('code-textarea');
    const diffView       = document.getElementById('code-diff-view');
    const filePreview    = document.getElementById('code-file-preview');
    const branchSelect   = document.getElementById('code-branch-select');
    const commitInput    = document.getElementById('code-commit-msg');
    const stagedList     = document.getElementById('code-staged-list');
    const changesList    = document.getElementById('code-changes-list');
    const stagedCount    = document.getElementById('code-staged-count');
    const changesCount   = document.getElementById('code-changes-count');
    const gitLog         = document.getElementById('code-git-log');
    const statusBranch   = document.getElementById('code-status-branch');
    const statusPos      = document.getElementById('code-status-pos');
    const statusLang     = document.getElementById('code-status-lang');
    const searchInput    = document.getElementById('code-search-input');
    const searchResults  = document.getElementById('code-search-results');

    // State
    let openFiles = [];
    let activeFile = null;
    let treeData = null;
    let currentBranch = 'main';
    let expandedDirs = new Set(['/']);
    let connectedRepo = null;   // {url, name, path}

    // ── Helpers ──────────────────────────────────────────────────
    async function api(url, opts = {}) {
        try {
            const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
            if (!r.ok) {
                const t = await r.text();
                throw new Error(t || r.statusText);
            }
            return await r.json();
        } catch (e) {
            console.error('[CodeIDE]', e);
            return null;
        }
    }

    const extToLang = {
        py: 'Python', js: 'JavaScript', ts: 'TypeScript', jsx: 'JavaScript JSX',
        tsx: 'TypeScript JSX', html: 'HTML', css: 'CSS', json: 'JSON', md: 'Markdown',
        sh: 'Shell', bash: 'Shell', yml: 'YAML', yaml: 'YAML', toml: 'TOML',
        tex: 'LaTeX', bib: 'BibTeX', txt: 'Plain Text', xml: 'XML', sql: 'SQL',
        dart: 'Dart', kt: 'Kotlin', swift: 'Swift', cpp: 'C++', c: 'C',
        h: 'C Header', hpp: 'C++ Header', java: 'Java', rs: 'Rust', go: 'Go',
        rb: 'Ruby', php: 'PHP', r: 'R', csv: 'CSV',
    };

    function extOf(path) { const m = path.match(/\.(\w+)$/); return m ? m[1].toLowerCase() : ''; }
    function langOf(path) { return extToLang[extOf(path)] || 'Plain Text'; }
    function fileIcon(name, isDir) {
        if (isDir) return '📂';
        const e = extOf(name);
        const m = { py: '🐍', js: '⬡', ts: '⬡', html: '🌐', css: '🎨', json: '{}', md: '📝', sh: '⚙', tex: '📄', toml: '⚙', yml: '⚙', yaml: '⚙' };
        return m[e] || '📄';
    }

    const previewExts = new Set([
        'pdf',
        'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico', 'bmp',
        'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a',
        'mp4', 'webm', 'mov', 'avi', 'mkv',
    ]);
    const imageExts = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico', 'bmp']);
    const videoExts = new Set(['mp4', 'webm', 'mov', 'avi', 'mkv']);
    const audioExts = new Set(['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a']);
    function isPreviewable(path) { return previewExts.has(extOf(path)); }

    function renderPreview(path) {
        if (!filePreview) return;
        const ext = extOf(path);
        const fname = path.split('/').pop();
        const src = `${API}/preview?path=${encodeURIComponent(path)}`;

        filePreview.className = 'code-file-preview';

        if (ext === 'pdf') {
            filePreview.classList.add('preview-pdf');
            filePreview.innerHTML = `
                <div class="pdf-wrapper">
                    <div class="pdf-header">
                        <span>📄 ${escapeHtml(fname)}</span>
                        <a href="${src}" target="_blank" title="Open in new tab">Open ↗</a>
                    </div>
                    <embed src="${src}" type="application/pdf" />
                </div>`;
        } else if (imageExts.has(ext)) {
            filePreview.innerHTML = `<div class="preview-filename">${escapeHtml(fname)}</div><img src="${src}" alt="${escapeHtml(fname)}" /><div class="preview-info">${ext.toUpperCase()} image</div>`;
        } else if (videoExts.has(ext)) {
            filePreview.innerHTML = `<div class="preview-icon">🎬</div><div class="preview-filename">${escapeHtml(fname)}</div><video controls preload="metadata"><source src="${src}"></video><div class="preview-info">${ext.toUpperCase()} video</div>`;
        } else if (audioExts.has(ext)) {
            filePreview.innerHTML = `<div class="preview-icon">🎵</div><div class="preview-filename">${escapeHtml(fname)}</div><audio controls preload="metadata"><source src="${src}"></audio><div class="preview-info">${ext.toUpperCase()} audio</div>`;
        } else {
            filePreview.innerHTML = `<div class="preview-filename">${escapeHtml(fname)}</div><div class="preview-info">${ext.toUpperCase()} file</div>`;
        }
    }

    // ══════════════════════════════════════════════════════════════
    //  Connect / Clone / Disconnect
    // ══════════════════════════════════════════════════════════════

    function showConnect() {
        if (connectScreen) connectScreen.style.display = '';
        if (ideEl) ideEl.style.display = 'none';
        connectedRepo = null;
        loadRecents();
    }

    function showIDE(name) {
        if (connectScreen) connectScreen.style.display = 'none';
        if (ideEl) ideEl.style.display = '';
        if (repoNameEl) repoNameEl.textContent = name;
        openFiles = [];
        activeFile = null;
        expandedDirs = new Set(['/']);
        renderTabs();
        if (welcomeEl) welcomeEl.style.display = '';
        if (editorContent) editorContent.style.display = 'none';
        if (diffView) diffView.style.display = 'none';
        if (filePreview) filePreview.style.display = 'none';
        loadTree();
        refreshGit();
    }

    async function doClone(url) {
        if (!url) return;
        if (connectStatus) {
            connectStatus.className = 'code-connect-status';
            connectStatus.innerHTML = '<span class="spinner"></span> Cloning repository…';
        }
        if (cloneBtn) cloneBtn.disabled = true;

        const data = await api(`${API}/clone`, {
            method: 'POST',
            body: JSON.stringify({ url })
        });

        if (cloneBtn) cloneBtn.disabled = false;

        if (!data || !data.ok) {
            if (connectStatus) {
                connectStatus.className = 'code-connect-status error';
                connectStatus.textContent = (data && data.detail) ? data.detail : 'Clone failed. Check the URL and try again.';
            }
            return;
        }

        if (connectStatus) {
            connectStatus.className = 'code-connect-status success';
            connectStatus.textContent = data.message || 'Connected!';
        }
        connectedRepo = { url, name: data.name, path: data.path };
        setTimeout(() => showIDE(data.name), 400);
    }

    async function doConnect(url, path) {
        const data = await api(`${API}/connect`, {
            method: 'POST',
            body: JSON.stringify({ url, path })
        });
        if (data && data.ok) {
            connectedRepo = { url, name: data.name, path };
            showIDE(data.name);
        }
    }

    async function doDisconnect() {
        await api(`${API}/disconnect`, { method: 'POST' });
        showConnect();
    }

    async function loadRecents() {
        const data = await api(`${API}/repos`);
        if (!data || !recentsEl) return;
        const repos = (data.repos || []).filter(r => r.exists);
        if (repos.length === 0) {
            recentsEl.innerHTML = '';
            return;
        }
        recentsEl.innerHTML = `
            <div class="code-connect-recents-title">Recent repositories</div>
            ${repos.map(r => `
                <div class="code-connect-recent" data-url="${r.url}" data-path="${r.path}">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M6 9v12"/></svg>
                    <div style="min-width:0;flex:1">
                        <div class="code-connect-recent-name">${escapeHtml(r.name || '')}</div>
                        <div class="code-connect-recent-url">${escapeHtml(r.url)}</div>
                    </div>
                    <button class="code-connect-recent-remove" data-path="${r.path}" title="Remove">✕</button>
                </div>
            `).join('')}
        `;
        recentsEl.querySelectorAll('.code-connect-recent').forEach(el => {
            el.addEventListener('click', (e) => {
                if (e.target.closest('.code-connect-recent-remove')) return;
                doConnect(el.dataset.url, el.dataset.path);
            });
        });
        recentsEl.querySelectorAll('.code-connect-recent-remove').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                await api(`${API}/delete-repo`, { method: 'POST', body: JSON.stringify({ path: btn.dataset.path }) });
                loadRecents();
            });
        });

        if (data.active) {
            const active = repos.find(r => r.path === data.active);
            if (active) {
                doConnect(active.url, active.path);
            }
        }
    }

    cloneBtn?.addEventListener('click', () => doClone(repoUrlInput?.value?.trim()));
    repoUrlInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') doClone(repoUrlInput.value.trim()); });
    disconnectBtn?.addEventListener('click', doDisconnect);

    // ── File tree ────────────────────────────────────────────────
    async function loadTree() {
        treeData = await api(`${API}/tree`);
        renderTree();
    }

    function renderTree() {
        if (!treeData || !fileTree) return;
        fileTree.innerHTML = '';
        renderNode(treeData, 0, '');
    }

    function renderNode(node, depth, parentPath) {
        if (!node) return;
        const items = node.children || [];
        const sorted = [...items].sort((a, b) => {
            if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
            return a.name.localeCompare(b.name);
        });
        for (const item of sorted) {
            const fullPath = parentPath ? `${parentPath}/${item.name}` : item.name;
            const el = document.createElement('div');
            el.className = 'code-tree-item' + (activeFile === fullPath ? ' active' : '');
            el.style.setProperty('--depth', depth);

            const isOpen = expandedDirs.has(fullPath);
            const openFile = openFiles.find(f => f.path === fullPath);
            const modClass = openFile?.modified ? ' modified' : '';

            if (item.is_dir) {
                el.innerHTML = `<span class="code-tree-caret${isOpen ? ' open' : ''}">▶</span><span class="tree-icon folder-icon">${fileIcon(item.name, true)}</span><span class="code-tree-name">${item.name}</span>`;
                el.addEventListener('click', () => {
                    if (expandedDirs.has(fullPath)) expandedDirs.delete(fullPath);
                    else expandedDirs.add(fullPath);
                    renderTree();
                });
            } else {
                const ext = extOf(item.name);
                el.innerHTML = `<span class="code-tree-caret" style="visibility:hidden">▶</span><span class="tree-icon file-icon ${ext}">${fileIcon(item.name, false)}</span><span class="code-tree-name${modClass}">${item.name}</span>`;
                el.addEventListener('click', () => openFileTab(fullPath));
            }
            fileTree.appendChild(el);

            if (item.is_dir && isOpen) {
                renderNode(item, depth + 1, fullPath);
            }
        }
    }

    // ── File tabs ────────────────────────────────────────────────
    function renderTabs() {
        if (!tabBar) return;
        tabBar.innerHTML = '';
        for (const f of openFiles) {
            const tab = document.createElement('div');
            tab.className = (f.path === activeFile ? 'active' : '') + (f.modified ? ' code-tab-modified' : '');
            tab.dataset.file = f.path;
            const name = f.path.split('/').pop();
            const ext = extOf(name);
            tab.innerHTML = `<span class="tree-icon file-icon ${ext}" style="font-size:11px">${fileIcon(name, false)}</span>${name}<span class="code-tab-close" data-file="${f.path}">×</span>`;
            tab.addEventListener('click', (e) => {
                if (e.target.classList.contains('code-tab-close')) {
                    closeFileTab(e.target.dataset.file);
                    return;
                }
                switchToFile(f.path);
            });
            tabBar.appendChild(tab);
        }
    }

    async function openFileTab(path) {
        let f = openFiles.find(x => x.path === path);
        if (!f) {
            if (isPreviewable(path)) {
                f = { path, content: '', originalContent: '', modified: false, preview: true };
            } else {
                const data = await api(`${API}/read?path=${encodeURIComponent(path)}`);
                if (!data) return;
                f = { path, content: data.content, originalContent: data.content, modified: false, preview: false };
            }
            openFiles.push(f);
        }
        switchToFile(path);
    }

    function switchToFile(path) {
        if (activeFile && textarea) {
            const cur = openFiles.find(x => x.path === activeFile);
            if (cur && !cur.preview) {
                cur.content = textarea.value;
                cur.modified = cur.content !== cur.originalContent;
            }
        }
        activeFile = path;
        const f = openFiles.find(x => x.path === path);
        if (!f) return;

        if (welcomeEl) welcomeEl.style.display = 'none';
        if (editorContent) editorContent.style.display = 'none';
        if (diffView) diffView.style.display = 'none';
        if (filePreview) filePreview.style.display = 'none';

        if (f.preview) {
            if (filePreview) {
                filePreview.style.display = '';
                renderPreview(path);
            }
        } else {
            if (editorContent) editorContent.style.display = '';
            if (textarea) textarea.value = f.content;
            updateCodeLineNumbers();
        }

        updateCodeBreadcrumb(path);
        if (statusLang) statusLang.textContent = f.preview ? (extOf(path).toUpperCase() + ' Preview') : langOf(path);
        updateCursorPos();
        renderTabs();
        renderTree();
    }

    function closeFileTab(path) {
        const idx = openFiles.findIndex(x => x.path === path);
        if (idx < 0) return;
        openFiles.splice(idx, 1);
        if (activeFile === path) {
            if (openFiles.length > 0) {
                switchToFile(openFiles[Math.min(idx, openFiles.length - 1)].path);
            } else {
                activeFile = null;
                if (welcomeEl) welcomeEl.style.display = '';
                if (editorContent) editorContent.style.display = 'none';
                if (diffView) diffView.style.display = 'none';
                if (filePreview) filePreview.style.display = 'none';
            }
        }
        renderTabs();
        renderTree();
    }

    // ── Line numbers & cursor ────────────────────────────────────
    let _codeLineCount = 0;
    function updateCodeLineNumbers() {
        if (!textarea || !lineNumbers) return;
        const lines = textarea.value.split('\n').length;
        if (lines === _codeLineCount) return;
        _codeLineCount = lines;
        let html = '';
        for (let i = 1; i <= lines; i++) html += `<span class="cln">${i}</span>`;
        lineNumbers.innerHTML = html;
    }

    function highlightCodeActiveLine() {
        if (!textarea || !lineNumbers) return;
        const pos = textarea.selectionStart;
        const lineNum = textarea.value.substring(0, pos).split('\n').length;
        lineNumbers.querySelectorAll('.cln').forEach((el, idx) => {
            el.classList.toggle('active', idx + 1 === lineNum);
        });
    }

    function updateCursorPos() {
        if (!textarea || !statusPos) return;
        const pos = textarea.selectionStart;
        const before = textarea.value.substring(0, pos);
        const ln = before.split('\n').length;
        const col = pos - before.lastIndexOf('\n');
        statusPos.textContent = `Ln ${ln}, Col ${col}`;
        highlightCodeActiveLine();
    }

    function syncCodeLineScroll() {
        if (lineNumbers && textarea) lineNumbers.scrollTop = textarea.scrollTop;
    }

    function updateCodeBreadcrumb(path) {
        if (!breadcrumb) return;
        const parts = ['hyphae', ...path.split('/')];
        breadcrumb.innerHTML = parts.map((p, i) =>
            (i > 0 ? '<span class="code-breadcrumb-sep">›</span>' : '') +
            `<span class="code-breadcrumb-seg">${p}</span>`
        ).join('');
    }

    if (textarea) {
        textarea.addEventListener('scroll', syncCodeLineScroll);
        textarea.addEventListener('input', () => {
            updateCodeLineNumbers();
            highlightCodeActiveLine();
            const f = openFiles.find(x => x.path === activeFile);
            if (f) {
                f.content = textarea.value;
                f.modified = f.content !== f.originalContent;
                renderTabs();
                renderTree();
            }
        });
        textarea.addEventListener('click', updateCursorPos);
        textarea.addEventListener('keyup', updateCursorPos);
        textarea.addEventListener('keydown', (e) => {
            if (e.key === 'Tab') {
                e.preventDefault();
                const start = textarea.selectionStart;
                const end = textarea.selectionEnd;
                textarea.value = textarea.value.substring(0, start) + '    ' + textarea.value.substring(end);
                textarea.selectionStart = textarea.selectionEnd = start + 4;
                textarea.dispatchEvent(new Event('input'));
            }
            if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                e.preventDefault();
                saveCurrentFile();
            }
        });
    }

    // ── Save file ────────────────────────────────────────────────
    async function saveCurrentFile() {
        if (!activeFile) return;
        const f = openFiles.find(x => x.path === activeFile);
        if (!f || f.preview) return;
        const res = await api(`${API}/write`, {
            method: 'POST',
            body: JSON.stringify({ path: f.path, content: f.content })
        });
        if (res) {
            f.originalContent = f.content;
            f.modified = false;
            renderTabs();
            renderTree();
        }
    }

    // ── Activity bar (sidebar panel switching) ───────────────────
    document.querySelectorAll('.code-activity-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.code-activity-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const panel = btn.dataset.panel;
            document.querySelectorAll('.code-sidebar-panel').forEach(p => p.style.display = 'none');
            const target = document.getElementById(`code-panel-${panel}`);
            if (target) target.style.display = '';
            if (panel === 'git') refreshGit();
        });
    });

    // ── Git section collapses ────────────────────────────────────
    document.getElementById('code-staged-header')?.addEventListener('click', () => {
        const list = document.getElementById('code-staged-list');
        const caret = document.querySelector('#code-staged-header .code-git-caret');
        if (list.style.display === 'none') { list.style.display = ''; caret.textContent = '▾'; }
        else { list.style.display = 'none'; caret.textContent = '▸'; }
    });
    document.getElementById('code-changes-header')?.addEventListener('click', () => {
        const list = document.getElementById('code-changes-list');
        const caret = document.querySelector('#code-changes-header .code-git-caret');
        if (list.style.display === 'none') { list.style.display = ''; caret.textContent = '▾'; }
        else { list.style.display = 'none'; caret.textContent = '▸'; }
    });
    document.getElementById('code-log-header')?.addEventListener('click', () => {
        if (gitLog.style.display === 'none') {
            gitLog.style.display = '';
            document.querySelector('#code-log-header .code-git-caret').textContent = '▾';
            loadGitLog();
        } else {
            gitLog.style.display = 'none';
            document.querySelector('#code-log-header .code-git-caret').textContent = '▸';
        }
    });

    // ── Git operations ───────────────────────────────────────────
    async function refreshGit() {
        const [status, branches] = await Promise.all([
            api(`${GIT}/status`),
            api(`${GIT}/branches`)
        ]);
        if (branches) {
            currentBranch = branches.current || 'main';
            if (branchSelect) {
                branchSelect.innerHTML = branches.all.map(b =>
                    `<option value="${b}"${b === currentBranch ? ' selected' : ''}>${b}</option>`
                ).join('');
            }
            if (statusBranch) {
                statusBranch.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M6 9v12"/></svg> ${currentBranch}`;
            }
        }
        if (status) {
            renderGitFiles(status.staged || [], stagedList, stagedCount, true);
            renderGitFiles(status.unstaged || [], changesList, changesCount, false);
        }
    }

    function renderGitFiles(files, listEl, countEl, isStaged) {
        if (!listEl) return;
        if (countEl) countEl.textContent = files.length;
        listEl.innerHTML = files.map(f => {
            const statusClass = f.status === 'M' ? 'modified' : f.status === 'A' ? 'added' : f.status === 'D' ? 'deleted' : 'untracked';
            const statusLabel = f.status === 'M' ? 'M' : f.status === 'A' ? 'A' : f.status === 'D' ? 'D' : '?';
            const name = f.path.split('/').pop();
            const dir = f.path.includes('/') ? f.path.substring(0, f.path.lastIndexOf('/')) : '';
            const actionBtn = isStaged
                ? `<button title="Unstage" data-action="unstage" data-path="${f.path}">−</button>`
                : `<button title="Stage" data-action="stage" data-path="${f.path}">+</button>`;
            return `<div class="code-git-file" data-path="${f.path}">
                <span class="git-status ${statusClass}">${statusLabel}</span>
                <span class="git-filename">${name}</span>
                ${dir ? `<span class="git-filepath">${dir}</span>` : ''}
                <span class="code-git-file-actions">
                    ${actionBtn}
                    <button title="View diff" data-action="diff" data-path="${f.path}">◑</button>
                </span>
            </div>`;
        }).join('');

    }

    function _handleGitListClick(e) {
        const btn = e.target.closest('button[data-action]');
        if (btn) {
            e.stopPropagation();
            const action = btn.dataset.action;
            const path = btn.dataset.path;
            if (action === 'stage') {
                api(`${GIT}/stage`, { method: 'POST', body: JSON.stringify({ paths: [path] }) }).then(refreshGit);
            } else if (action === 'unstage') {
                api(`${GIT}/unstage`, { method: 'POST', body: JSON.stringify({ paths: [path] }) }).then(refreshGit);
            } else if (action === 'diff') {
                showDiff(path);
            }
            return;
        }
        const row = e.target.closest('.code-git-file');
        if (row) openFileTab(row.dataset.path);
    }
    stagedList?.addEventListener('click', _handleGitListClick);
    changesList?.addEventListener('click', _handleGitListClick);

    async function showDiff(path) {
        const data = await api(`${GIT}/diff?path=${encodeURIComponent(path)}`);
        if (!data || !diffView) return;
        if (welcomeEl) welcomeEl.style.display = 'none';
        if (editorContent) editorContent.style.display = 'none';
        if (filePreview) filePreview.style.display = 'none';
        diffView.style.display = '';
        const lines = (data.diff || '').split('\n');
        diffView.innerHTML = lines.map(line => {
            if (line.startsWith('@@')) return `<div class="code-diff-line header">${escapeHtml(line)}</div>`;
            if (line.startsWith('+')) return `<div class="code-diff-line added">${escapeHtml(line)}</div>`;
            if (line.startsWith('-')) return `<div class="code-diff-line removed">${escapeHtml(line)}</div>`;
            return `<div class="code-diff-line context">${escapeHtml(line)}</div>`;
        }).join('');
    }

    async function loadGitLog() {
        const data = await api(`${GIT}/log`);
        if (!data || !gitLog) return;
        gitLog.innerHTML = (data.commits || []).map(c => `
            <div class="code-git-log-item">
                <div class="code-git-log-msg">${escapeHtml(c.message)}</div>
                <div class="code-git-log-meta">
                    <span class="code-git-log-hash">${c.hash?.substring(0, 7) || ''}</span>
                    <span>${escapeHtml(c.author || '')}</span>
                    <span>${c.date || ''}</span>
                </div>
            </div>
        `).join('');
    }

    document.getElementById('code-git-pull-btn')?.addEventListener('click', async () => {
        const res = await api(`${GIT}/pull`, { method: 'POST' });
        if (res) { refreshGit(); loadTree(); }
    });
    document.getElementById('code-git-push-btn')?.addEventListener('click', async () => {
        await api(`${GIT}/push`, { method: 'POST' });
    });
    document.getElementById('code-git-commit-btn')?.addEventListener('click', async () => {
        const msg = commitInput?.value?.trim();
        if (!msg) { commitInput?.focus(); return; }
        const res = await api(`${GIT}/commit`, { method: 'POST', body: JSON.stringify({ message: msg }) });
        if (res) {
            commitInput.value = '';
            refreshGit();
            loadGitLog();
        }
    });

    branchSelect?.addEventListener('change', async () => {
        const branch = branchSelect.value;
        await api(`${GIT}/checkout`, { method: 'POST', body: JSON.stringify({ branch }) });
        currentBranch = branch;
        if (statusBranch) statusBranch.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M6 9v12"/></svg> ${branch}`;
        loadTree();
        openFiles = [];
        activeFile = null;
        renderTabs();
        if (welcomeEl) welcomeEl.style.display = '';
        if (editorContent) editorContent.style.display = 'none';
        if (diffView) diffView.style.display = 'none';
    });

    document.getElementById('code-new-branch-btn')?.addEventListener('click', async () => {
        const name = prompt('New branch name:');
        if (!name || !name.trim()) return;
        await api(`${GIT}/checkout`, { method: 'POST', body: JSON.stringify({ branch: name.trim(), create: true }) });
        refreshGit();
        loadTree();
    });

    // ── New file / folder ────────────────────────────────────────
    document.getElementById('code-new-file-btn')?.addEventListener('click', async () => {
        const name = prompt('New file path (relative to repo root):');
        if (!name || !name.trim()) return;
        await api(`${API}/write`, { method: 'POST', body: JSON.stringify({ path: name.trim(), content: '' }) });
        await loadTree();
        openFileTab(name.trim());
    });
    document.getElementById('code-new-folder-btn')?.addEventListener('click', async () => {
        const name = prompt('New folder path (relative to repo root):');
        if (!name || !name.trim()) return;
        await api(`${API}/mkdir`, { method: 'POST', body: JSON.stringify({ path: name.trim() }) });
        expandedDirs.add(name.trim());
        loadTree();
    });
    document.getElementById('code-refresh-btn')?.addEventListener('click', () => loadTree());

    // ── Search ───────────────────────────────────────────────────
    let _searchTimer = null;
    searchInput?.addEventListener('input', () => {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(doSearch, 400);
    });
    document.getElementById('code-search-btn')?.addEventListener('click', doSearch);
    searchInput?.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });

    async function doSearch() {
        const q = searchInput?.value?.trim();
        if (!q || !searchResults) return;
        searchResults.innerHTML = '<div style="padding:12px;color:#888;">Searching…</div>';
        const data = await api(`${API}/search?q=${encodeURIComponent(q)}`);
        if (!data || !data.results || data.results.length === 0) {
            searchResults.innerHTML = '<div style="padding:12px;color:#888;">No results found.</div>';
            return;
        }
        searchResults.innerHTML = data.results.map(r => `
            <div class="code-search-result-file">${r.file}</div>
            ${r.matches.map(m =>
                `<div class="code-search-result-line" data-file="${r.file}" data-line="${m.line}">${m.line}: ${m.text.replace(new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), match => `<span class="code-search-match">${match}</span>`)}</div>`
            ).join('')}
        `).join('');
    }
    searchResults?.addEventListener('click', (e) => {
        const line = e.target.closest('.code-search-result-line');
        if (line) openFileTab(line.dataset.file);
    });

    // ── Keyboard shortcut: Ctrl+P quick open ─────────────────────
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'p' && mainPanelCode?.style.display !== 'none') {
            e.preventDefault();
            const name = prompt('Quick open — type file path:');
            if (name) openFileTab(name);
        }
    });

    // ── Public interface ─────────────────────────────────────────
    function refresh() {
        if (connectedRepo) {
            loadTree();
            refreshGit();
        } else {
            init();
        }
    }

    function init() {
        showConnect();
    }

    setTimeout(() => { init(); }, 100);

    return { refresh, init, loadTree, refreshGit, openFileTab, showConnect, showIDE };
})();
