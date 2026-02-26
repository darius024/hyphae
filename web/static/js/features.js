// ══════════════════════════════════════════════════════════════════════════════
//   Features: Tags, Document Links, Analytics, Deadlines, Versions, Orgs
// ══════════════════════════════════════════════════════════════════════════════

const Features = (() => {
    const API = '/api';

    // ── State ─────────────────────────────────────────────────────────────
    let allTags = [];
    let allLinks = [];
    let allDeadlines = [];
    let editingDeadlineId = null;
    let selectedVersionId = null;
    
    // ── Element References ────────────────────────────────────────────────
    const tagsModalOverlay = document.getElementById('tags-modal-overlay');
    const tagsModalClose = document.getElementById('tags-modal-close');
    const tagNameInput = document.getElementById('tag-name-input');
    const tagColorInput = document.getElementById('tag-color-input');
    const tagCreateBtn = document.getElementById('tag-create-btn');
    const tagsList = document.getElementById('tags-list');
    const tagsSourceSection = document.getElementById('tags-source-section');
    const tagsSourceName = document.getElementById('tags-source-name');
    const tagsSourceList = document.getElementById('tags-source-list');
    
    const linksModalOverlay = document.getElementById('links-modal-overlay');
    const linksModalClose = document.getElementById('links-modal-close');
    const linkCreateBtn = document.getElementById('link-create-btn');
    const linkTypeFilter = document.getElementById('link-type-filter');
    const linksList = document.getElementById('links-list');
    const linksGraphCanvas = document.getElementById('links-graph-canvas');
    const linkCreateForm = document.getElementById('link-create-form');
    const linkSourceSelect = document.getElementById('link-source-select');
    const linkTargetSelect = document.getElementById('link-target-select');
    const linkTypeSelect = document.getElementById('link-type-select');
    const linkDescInput = document.getElementById('link-description-input');
    const linkSaveBtn = document.getElementById('link-save-btn');
    const linkCancelBtn = document.getElementById('link-cancel-btn');
    
    const analyticsModalOverlay = document.getElementById('analytics-modal-overlay');
    const analyticsModalClose = document.getElementById('analytics-modal-close');
    const analyticsChart = document.getElementById('analytics-chart');
    const analyticsTotalQueries = document.getElementById('analytics-total-queries');
    const analyticsAvgLatency = document.getElementById('analytics-avg-latency');
    const analyticsLocalCloud = document.getElementById('analytics-local-cloud');
    const analyticsTopActions = document.getElementById('analytics-top-actions');
    const analyticsRecentList = document.getElementById('analytics-recent-list');
    
    const calendarSyncOverlay = document.getElementById('calendar-sync-overlay');
    const calendarSyncClose = document.getElementById('calendar-sync-close');
    const calendarSyncConnected = document.getElementById('calendar-sync-connected');
    const calendarSyncProviderName = document.getElementById('calendar-sync-provider-name');
    const calendarSyncLast = document.getElementById('calendar-sync-last');
    
    const deadlineModalOverlay = document.getElementById('deadline-modal-overlay');
    const deadlineModalClose = document.getElementById('deadline-modal-close');
    const deadlineModalTitleText = document.getElementById('deadline-modal-title-text');
    const deadlineTitleInput = document.getElementById('deadline-title-input');
    const deadlineDateInput = document.getElementById('deadline-date-input');
    const deadlinePriorityInput = document.getElementById('deadline-priority-input');
    const deadlineReminderInput = document.getElementById('deadline-reminder-input');
    const deadlineSourceInput = document.getElementById('deadline-source-input');
    const deadlineNoteInput = document.getElementById('deadline-note-input');
    const deadlineSaveBtn = document.getElementById('deadline-save-btn');
    const deadlineCancelBtn = document.getElementById('deadline-cancel-btn');
    
    const versionsModalOverlay = document.getElementById('versions-modal-overlay');
    const versionsModalClose = document.getElementById('versions-modal-close');
    const versionsList = document.getElementById('versions-list');
    const versionsPreviewTitle = document.getElementById('versions-preview-title');
    const versionsPreviewContent = document.getElementById('versions-preview-content');
    const versionsRestoreBtn = document.getElementById('versions-restore-btn');
    
    // Buttons to open modals
    const nbTagsBtn = document.getElementById('nb-tags-btn');
    const nbLinksBtn = document.getElementById('nb-links-btn');
    const analyticsBtn = document.getElementById('analytics-btn');
    const calDeadlineBtn = document.getElementById('cal-deadline-btn');
    const calSyncBtn = document.getElementById('cal-sync-btn');
    const paperVersionsBtn = document.getElementById('paper-versions-btn');
    
    // ══════════════════════════════════════════════════════════════════════
    //   TAGS
    // ══════════════════════════════════════════════════════════════════════
    
    let currentTagSourceId = null;
    
    async function loadTags() {
        try {
            const resp = await fetch(`${API}/tags`);
            if (resp.ok) {
                const data = await resp.json();
                allTags = data.tags || [];
                renderTags();
            }
        } catch (e) {
            console.warn('Failed to load tags:', e);
        }
    }
    
    function renderTags() {
        if (!tagsList) return;
        tagsList.innerHTML = '';
        allTags.forEach(tag => {
            const chip = document.createElement('span');
            chip.className = 'tag-chip';
            chip.innerHTML = `
                <span class="tag-dot" style="background:${tag.color}"></span>
                ${escapeHtml(tag.name)}
                <button class="tag-delete" data-id="${tag.id}">&times;</button>
            `;
            chip.querySelector('.tag-delete').onclick = (e) => {
                e.stopPropagation();
                deleteTag(tag.id);
            };
            tagsList.appendChild(chip);
        });
    }
    
    async function createTag() {
        const name = tagNameInput?.value.trim();
        const color = tagColorInput?.value || '#6366f1';
        if (!name) return;
        
        try {
            const resp = await fetch(`${API}/tags`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, color })
            });
            if (resp.ok) {
                tagNameInput.value = '';
                await loadTags();
                showToast('Tag created', 'success');
            }
        } catch (e) {
            showToast('Failed to create tag', 'error');
        }
    }
    
    async function deleteTag(tagId) {
        try {
            const resp = await fetch(`${API}/tags/${tagId}`, { method: 'DELETE' });
            if (resp.ok) {
                await loadTags();
                showToast('Tag deleted', 'success');
            }
        } catch (e) {
            showToast('Failed to delete tag', 'error');
        }
    }
    
    function showTagsForSource(sourceId, sourceName) {
        currentTagSourceId = sourceId;
        if (tagsSourceSection) tagsSourceSection.style.display = 'block';
        if (tagsSourceName) tagsSourceName.textContent = sourceName;
        renderSourceTags();
    }
    
    async function renderSourceTags() {
        if (!tagsSourceList || !currentTagSourceId || !window.currentNotebook) return;
        
        let sourceTags = [];
        try {
            const resp = await fetch(`${API}/notebooks/${window.currentNotebook}/sources/${currentTagSourceId}/tags`);
            if (resp.ok) {
                const data = await resp.json();
                sourceTags = data.tags || [];
            }
        } catch (e) {}
        
        const sourceTagIds = new Set(sourceTags.map(t => t.id));
        
        tagsSourceList.innerHTML = '';
        allTags.forEach(tag => {
            const chip = document.createElement('span');
            chip.className = 'tag-chip' + (sourceTagIds.has(tag.id) ? ' active' : '');
            chip.innerHTML = `<span class="tag-dot" style="background:${tag.color}"></span>${escapeHtml(tag.name)}`;
            chip.onclick = () => toggleTagOnSource(tag.id, sourceTagIds.has(tag.id));
            tagsSourceList.appendChild(chip);
        });
    }
    
    async function toggleTagOnSource(tagId, isActive) {
        if (!currentTagSourceId || !window.currentNotebook) return;
        
        let currentTagIds = [];
        try {
            const resp = await fetch(`${API}/notebooks/${window.currentNotebook}/sources/${currentTagSourceId}/tags`);
            if (resp.ok) {
                const data = await resp.json();
                currentTagIds = (data.tags || []).map(t => t.id);
            }
        } catch (e) {}
        
        if (isActive) {
            currentTagIds = currentTagIds.filter(id => id !== tagId);
        } else {
            currentTagIds.push(tagId);
        }
        
        try {
            await fetch(`${API}/notebooks/${window.currentNotebook}/sources/${currentTagSourceId}/tags`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag_ids: currentTagIds })
            });
            await renderSourceTags();
            if (typeof renderSourceList === 'function') renderSourceList();
        } catch (e) {
            showToast('Failed to update tag', 'error');
        }
    }
    
    function showTagsModal(sourceId, sourceName) {
        loadTags();
        if (sourceId && sourceName) {
            showTagsForSource(sourceId, sourceName);
        } else {
            if (tagsSourceSection) tagsSourceSection.style.display = 'none';
            currentTagSourceId = null;
        }
        tagsModalOverlay?.classList.add('show');
    }
    
    function hideTagsModal() {
        tagsModalOverlay?.classList.remove('show');
    }
    
    // ══════════════════════════════════════════════════════════════════════
    //   DOCUMENT LINKS (Knowledge Graph)
    // ══════════════════════════════════════════════════════════════════════
    
    async function loadLinks() {
        if (!window.currentNotebook) return;
        try {
            const resp = await fetch(`${API}/notebooks/${window.currentNotebook}/graph`);
            if (resp.ok) {
                const data = await resp.json();
                allLinks = data.edges || [];
                renderLinks();
                renderGraph(data.nodes || [], data.edges || []);
            }
        } catch (e) {
            console.warn('Failed to load links:', e);
        }
    }
    
    function renderLinks() {
        if (!linksList) return;
        linksList.innerHTML = '';
        
        if (allLinks.length === 0) {
            linksList.innerHTML = '<p style="color:var(--text-secondary);font-size:12px;">No links yet. Create one to connect your documents.</p>';
            return;
        }
        
        allLinks.forEach(link => {
            const item = document.createElement('div');
            item.className = 'link-item';
            item.innerHTML = `
                <div class="link-item-header">
                    <span class="link-item-type">${escapeHtml(link.type)}</span>
                    <button class="link-item-delete" data-id="${link.id}">&times;</button>
                </div>
                <div class="link-item-docs">
                    <strong>${escapeHtml(link.source_label || 'Doc')}</strong>
                    → <strong>${escapeHtml(link.target_label || 'Doc')}</strong>
                </div>
                ${link.note ? `<div style="font-size:11px;color:var(--text-secondary);margin-top:4px">${escapeHtml(link.note)}</div>` : ''}
            `;
            item.querySelector('.link-item-delete').onclick = () => deleteLink(link.id);
            linksList.appendChild(item);
        });
    }
    
    async function deleteLink(linkId) {
        if (!window.currentNotebook) return;
        try {
            const resp = await fetch(`${API}/notebooks/${window.currentNotebook}/links/${linkId}`, { method: 'DELETE' });
            if (resp.ok) {
                await loadLinks();
                showToast('Link deleted', 'success');
            }
        } catch (e) {
            showToast('Failed to delete link', 'error');
        }
    }
    
    function renderGraph(nodes, edges) {
        const canvas = linksGraphCanvas;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const container = canvas.parentElement;
        
        canvas.width = container.clientWidth;
        canvas.height = container.clientHeight;
        
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        if (nodes.length === 0) {
            ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-secondary');
            ctx.font = '14px Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('No connections yet', canvas.width / 2, canvas.height / 2);
            return;
        }
        
        const centerX = canvas.width / 2;
        const centerY = canvas.height / 2;
        const radius = Math.min(canvas.width, canvas.height) * 0.35;
        
        const nodeMap = new Map();
        nodes.forEach((node, i) => {
            const angle = (i / nodes.length) * Math.PI * 2 - Math.PI / 2;
            node.x = centerX + Math.cos(angle) * radius;
            node.y = centerY + Math.sin(angle) * radius;
            nodeMap.set(node.id, node);
        });
        
        const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#6366f1';
        const textColor = getComputedStyle(document.documentElement).getPropertyValue('--text-primary') || '#1a1a2e';
        
        ctx.strokeStyle = accent;
        ctx.lineWidth = 1.5;
        ctx.globalAlpha = 0.5;
        edges.forEach(edge => {
            const source = nodeMap.get(edge.source);
            const target = nodeMap.get(edge.target);
            if (source && target) {
                ctx.beginPath();
                ctx.moveTo(source.x, source.y);
                ctx.lineTo(target.x, target.y);
                ctx.stroke();
            }
        });
        ctx.globalAlpha = 1;
        
        nodes.forEach(node => {
            const nodeColor = node.tags?.[0]?.color || accent;
            ctx.beginPath();
            ctx.arc(node.x, node.y, 24, 0, Math.PI * 2);
            ctx.fillStyle = nodeColor;
            ctx.fill();
            
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 10px Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            const shortName = node.label.length > 8 ? node.label.slice(0, 8) + '…' : node.label;
            ctx.fillText(shortName, node.x, node.y);
        });
    }
    
    function populateLinkSelects() {
        if (!window.sources) return;
        const options = window.sources.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
        if (linkSourceSelect) linkSourceSelect.innerHTML = options;
        if (linkTargetSelect) linkTargetSelect.innerHTML = options;
    }
    
    function showLinkCreateForm() {
        populateLinkSelects();
        if (linkCreateForm) linkCreateForm.style.display = 'block';
    }
    
    function hideLinkCreateForm() {
        if (linkCreateForm) linkCreateForm.style.display = 'none';
        if (linkDescInput) linkDescInput.value = '';
    }
    
    async function saveLink() {
        if (!window.currentNotebook) return;
        const source_id = linkSourceSelect?.value;
        const target_id = linkTargetSelect?.value;
        const link_type = linkTypeSelect?.value || 'related';
        const note = linkDescInput?.value.trim() || null;
        
        if (!source_id || !target_id || source_id === target_id) {
            showToast('Please select different source and target documents', 'warn');
            return;
        }
        
        try {
            const resp = await fetch(`${API}/notebooks/${window.currentNotebook}/sources/${source_id}/links`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_id, link_type, note })
            });
            if (resp.ok) {
                hideLinkCreateForm();
                await loadLinks();
                showToast('Link created', 'success');
            }
        } catch (e) {
            showToast('Failed to create link', 'error');
        }
    }
    
    function showLinksModal() {
        loadLinks();
        linksModalOverlay?.classList.add('show');
    }
    
    function hideLinksModal() {
        linksModalOverlay?.classList.remove('show');
        hideLinkCreateForm();
    }
    
    // ══════════════════════════════════════════════════════════════════════
    //   ANALYTICS
    // ══════════════════════════════════════════════════════════════════════
    
    async function loadAnalytics(days = 7) {
        try {
            const resp = await fetch(`${API}/analytics/dashboard?days=${days}`);
            if (resp.ok) {
                const data = await resp.json();
                renderAnalytics(data);
            }
        } catch (e) {
            console.warn('Failed to load analytics:', e);
        }
    }
    
    function renderAnalytics(data) {
        if (analyticsTotalQueries) analyticsTotalQueries.textContent = data.total_events || 0;
        if (analyticsAvgLatency) analyticsAvgLatency.textContent = `${Math.round(data.avg_latency_ms || 0)}ms`;
        if (analyticsLocalCloud) {
            const rd = data.route_distribution || {};
            const local = rd['local'] || 0;
            const cloud = rd['cloud'] || 0;
            analyticsLocalCloud.textContent = `${local} / ${cloud}`;
        }
        
        if (analyticsTopActions && data.events_by_type) {
            const types = Object.entries(data.events_by_type)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 3);
            analyticsTopActions.innerHTML = types.map(([t, c]) => `${t}: ${c}`).join('<br>');
        }
        
        if (analyticsChart && data.events_per_day) {
            analyticsChart.innerHTML = '';
            const max = Math.max(...data.events_per_day.map(d => d.count), 1);
            data.events_per_day.forEach(d => {
                const bar = document.createElement('div');
                bar.className = 'analytics-chart-bar';
                bar.style.height = `${(d.count / max) * 100}%`;
                bar.title = `${d.day}: ${d.count} events`;
                analyticsChart.appendChild(bar);
            });
        }
    }
    
    function showAnalyticsModal() {
        loadAnalytics(7);
        analyticsModalOverlay?.classList.add('show');
    }
    
    function hideAnalyticsModal() {
        analyticsModalOverlay?.classList.remove('show');
    }
    
    window.logUsage = async function(action, route, latency_ms, data_local, query_preview) {
        try {
            await fetch(`${API}/usage/log`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    action,
                    route,
                    latency_ms,
                    data_local,
                    query_preview: query_preview?.slice(0, 100),
                    notebook_id: window.currentNotebook
                })
            });
        } catch (e) {
            // Silent fail
        }
    };
    
    // ══════════════════════════════════════════════════════════════════════
    //   CALENDAR SYNC
    // ══════════════════════════════════════════════════════════════════════
    
    function showCalendarSyncModal() {
        calendarSyncOverlay?.classList.add('show');
    }
    
    function hideCalendarSyncModal() {
        calendarSyncOverlay?.classList.remove('show');
    }
    
    async function connectCalendar(provider) {
        showToast(`${provider} calendar sync coming soon!`, 'info');
    }
    
    // ══════════════════════════════════════════════════════════════════════
    //   DEADLINES
    // ══════════════════════════════════════════════════════════════════════
    
    async function loadDeadlines() {
        if (!window.currentNotebook) return;
        try {
            const resp = await fetch(`${API}/deadlines?notebook_id=${window.currentNotebook}`);
            if (resp.ok) {
                const data = await resp.json();
                allDeadlines = data.deadlines || [];
            }
        } catch (e) {
            console.warn('Failed to load deadlines:', e);
        }
    }
    
    function showDeadlineModal(deadline = null) {
        editingDeadlineId = deadline?.id || null;
        if (deadlineModalTitleText) {
            deadlineModalTitleText.textContent = deadline ? 'Edit Deadline' : 'New Deadline';
        }
        
        if (deadlineSourceInput && window.sources) {
            deadlineSourceInput.innerHTML = '<option value="">None</option>' +
                window.sources.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
        }
        
        if (deadline) {
            if (deadlineTitleInput) deadlineTitleInput.value = deadline.title || '';
            if (deadlineDateInput) deadlineDateInput.value = deadline.due_date?.slice(0, 16) || '';
            if (deadlinePriorityInput) deadlinePriorityInput.value = deadline.priority || 'medium';
            if (deadlineSourceInput) deadlineSourceInput.value = deadline.source_id || '';
            if (deadlineNoteInput) deadlineNoteInput.value = deadline.note || '';
        } else {
            if (deadlineTitleInput) deadlineTitleInput.value = '';
            if (deadlineDateInput) deadlineDateInput.value = '';
            if (deadlinePriorityInput) deadlinePriorityInput.value = 'medium';
            if (deadlineReminderInput) deadlineReminderInput.value = '1d';
            if (deadlineSourceInput) deadlineSourceInput.value = '';
            if (deadlineNoteInput) deadlineNoteInput.value = '';
        }
        
        deadlineModalOverlay?.classList.add('show');
    }
    
    function hideDeadlineModal() {
        deadlineModalOverlay?.classList.remove('show');
        editingDeadlineId = null;
    }
    
    async function saveDeadline() {
        if (!window.currentNotebook) return;
        
        const title = deadlineTitleInput?.value.trim();
        const due_date = deadlineDateInput?.value;
        const priority = deadlinePriorityInput?.value || 'medium';
        const source_id = deadlineSourceInput?.value ? parseInt(deadlineSourceInput.value) : null;
        const note = deadlineNoteInput?.value.trim() || null;
        
        if (!title || !due_date) {
            showToast('Title and due date are required', 'warn');
            return;
        }
        
        const reminderSelect = deadlineReminderInput?.value;
        let reminder_at = null;
        if (reminderSelect) {
            const dueMs = new Date(due_date).getTime();
            const offsets = { '1h': 3600000, '1d': 86400000, '3d': 259200000, '1w': 604800000 };
            if (offsets[reminderSelect]) {
                reminder_at = new Date(dueMs - offsets[reminderSelect]).toISOString();
            }
        }
        
        try {
            if (editingDeadlineId) {
                await fetch(`${API}/deadlines/${editingDeadlineId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, due_date, priority, source_id, note, reminder_at })
                });
                showToast('Deadline updated', 'success');
            } else {
                await fetch(`${API}/deadlines`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        notebook_id: window.currentNotebook,
                        title, 
                        due_date, 
                        priority, 
                        source_id, 
                        note, 
                        reminder_at 
                    })
                });
                showToast('Deadline created', 'success');
            }
            
            hideDeadlineModal();
            await loadDeadlines();
            if (typeof renderCalendarMain === 'function') renderCalendarMain();
        } catch (e) {
            showToast('Failed to save deadline', 'error');
        }
    }
    
    // ══════════════════════════════════════════════════════════════════════
    //   VERSION HISTORY
    // ══════════════════════════════════════════════════════════════════════
    
    let currentNoteId = 'write-main';
    let allVersions = [];
    
    async function loadVersions(noteId) {
        if (!window.currentNotebook) return;
        currentNoteId = noteId || currentNoteId;
        try {
            const resp = await fetch(`${API}/notebooks/${window.currentNotebook}/notes/${encodeURIComponent(currentNoteId)}/versions`);
            if (resp.ok) {
                const data = await resp.json();
                allVersions = data.versions || [];
                renderVersions();
            }
        } catch (e) {
            console.warn('Failed to load versions:', e);
            allVersions = [];
            renderVersions();
        }
    }
    
    function renderVersions() {
        if (!versionsList) return;
        versionsList.innerHTML = '';
        
        if (allVersions.length === 0) {
            versionsList.innerHTML = '<p style="color:var(--text-secondary);font-size:12px;">No versions saved yet.</p>';
            return;
        }
        
        allVersions.forEach(v => {
            const item = document.createElement('div');
            item.className = 'version-item' + (selectedVersionId === v.id ? ' active' : '');
            item.innerHTML = `
                <div class="version-item-header">
                    <span class="version-item-num">v${v.version_num}</span>
                    <span class="version-item-date">${new Date(v.created_at).toLocaleString()}</span>
                </div>
                <div class="version-item-summary">${escapeHtml(v.change_summary || 'No description')}</div>
            `;
            item.onclick = () => selectVersion(v);
            versionsList.appendChild(item);
        });
    }
    
    async function selectVersion(version) {
        if (!window.currentNotebook) return;
        selectedVersionId = version.version_num;
        renderVersions();
        
        try {
            const resp = await fetch(`${API}/notebooks/${window.currentNotebook}/notes/${encodeURIComponent(currentNoteId)}/versions/${version.version_num}`);
            if (resp.ok) {
                const data = await resp.json();
                if (versionsPreviewTitle) versionsPreviewTitle.textContent = `Version ${version.version_num}`;
                if (versionsPreviewContent) versionsPreviewContent.textContent = data.content || '';
                if (versionsRestoreBtn) versionsRestoreBtn.style.display = 'inline-block';
            }
        } catch (e) {
            if (versionsPreviewContent) versionsPreviewContent.textContent = 'Failed to load version content';
        }
    }
    
    async function restoreVersion() {
        if (!selectedVersionId || !window.currentNotebook) return;
        try {
            const resp = await fetch(`${API}/notebooks/${window.currentNotebook}/notes/${encodeURIComponent(currentNoteId)}/restore/${selectedVersionId}`, {
                method: 'POST'
            });
            if (resp.ok) {
                const noteResp = await fetch(`${API}/notebooks/${window.currentNotebook}/notes/${encodeURIComponent(currentNoteId)}`);
                if (noteResp.ok) {
                    const data = await noteResp.json();
                    const editor = document.getElementById('latex-source-main');
                    if (editor) {
                        editor.value = data.content || '';
                        editor.dispatchEvent(new Event('input'));
                    }
                }
                hideVersionsModal();
                showToast('Version restored', 'success');
            }
        } catch (e) {
            showToast('Failed to restore version', 'error');
        }
    }
    
    async function saveVersion(content, changeSummary) {
        if (!window.currentNotebook) return;
        try {
            await fetch(`${API}/notebooks/${window.currentNotebook}/notes`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: currentNoteId,
                    title: 'Write Document',
                    content: content
                })
            });
        } catch (e) {
            console.warn('Failed to save version:', e);
        }
    }
    
    function showVersionsModal() {
        loadVersions(currentNoteId);
        selectedVersionId = null;
        if (versionsPreviewTitle) versionsPreviewTitle.textContent = 'Select a version to preview';
        if (versionsPreviewContent) versionsPreviewContent.innerHTML = '<p class="versions-empty">Select a version from the list to preview its content.</p>';
        if (versionsRestoreBtn) versionsRestoreBtn.style.display = 'none';
        versionsModalOverlay?.classList.add('show');
    }
    
    function hideVersionsModal() {
        versionsModalOverlay?.classList.remove('show');
    }
    
    let lastContent = '';
    function setupAutoVersionSave() {
        const editor = document.getElementById('latex-source-main');
        if (!editor) return;
        
        setInterval(() => {
            const content = editor.value;
            if (content && content !== lastContent && content.length > 50) {
                saveVersion(content, 'Auto-save');
                lastContent = content;
            }
        }, 5 * 60 * 1000);
    }

    // ══════════════════════════════════════════════════════════════════════
    //   ORGANIZATIONS
    // ══════════════════════════════════════════════════════════════════════
    
    const orgsModalOverlay = document.getElementById('orgs-modal-overlay');
    const orgsModalClose = document.getElementById('orgs-modal-close');
    const orgsList = document.getElementById('orgs-list');
    const orgsDetailPanel = document.getElementById('orgs-detail-panel');
    const orgCreateForm = document.getElementById('org-create-form');
    const orgInviteForm = document.getElementById('org-invite-form');
    const organizationsBtn = document.getElementById('organizations-btn');
    
    let currentOrgId = null;
    let allOrgs = [];
    
    async function loadOrganizations() {
        try {
            const resp = await fetch(`${API}/organizations`, {
                headers: window.currentUser ? { 'X-User-Id': window.currentUser.id } : {}
            });
            if (resp.ok) {
                const data = await resp.json();
                allOrgs = data.organizations || [];
                renderOrgsList();
            }
        } catch (e) {
            console.warn('Failed to load organizations:', e);
        }
    }
    
    function renderOrgsList() {
        if (!orgsList) return;
        
        if (allOrgs.length === 0) {
            orgsList.innerHTML = `
                <div class="orgs-empty">
                    <p>You're not part of any organizations yet.</p>
                    <p>Create one to start collaborating!</p>
                </div>
            `;
            return;
        }
        
        orgsList.innerHTML = allOrgs.map(org => `
            <div class="org-item" data-org-id="${org.id}">
                <div class="org-item-avatar">
                    ${org.avatar_url ? `<img src="${org.avatar_url}" alt="">` : '👥'}
                </div>
                <div class="org-item-info">
                    <div class="org-item-name">${escapeHtml(org.name)}</div>
                    <div class="org-item-meta">
                        <span>${org.member_count || 0} members</span>
                        <span>•</span>
                        <span>${org.notebook_count || 0} notebooks</span>
                    </div>
                </div>
                <span class="org-item-role">${org.user_role || 'member'}</span>
            </div>
        `).join('');
        
        orgsList.querySelectorAll('.org-item').forEach(item => {
            item.addEventListener('click', () => loadOrgDetails(item.dataset.orgId));
        });
    }
    
    async function loadOrgDetails(orgId) {
        currentOrgId = orgId;
        try {
            const resp = await fetch(`${API}/organizations/${orgId}`, {
                headers: window.currentUser ? { 'X-User-Id': window.currentUser.id } : {}
            });
            if (resp.ok) {
                const org = await resp.json();
                renderOrgDetails(org);
            }
        } catch (e) {
            showToast('Failed to load organization', 'error');
        }
    }
    
    function renderOrgDetails(org) {
        const nameEl = document.getElementById('org-detail-name');
        const descEl = document.getElementById('org-detail-desc');
        const membersCount = document.getElementById('org-members-count');
        const membersList = document.getElementById('org-members-list');
        const notebooksCount = document.getElementById('org-notebooks-count');
        const notebooksList = document.getElementById('org-notebooks-list');
        
        if (nameEl) nameEl.textContent = org.name;
        if (descEl) descEl.textContent = org.description || 'No description';
        if (membersCount) membersCount.textContent = `${org.members?.length || 0} members`;
        if (notebooksCount) notebooksCount.textContent = `${org.notebooks?.length || 0} notebooks`;
        
        if (membersList && org.members) {
            membersList.innerHTML = org.members.map(m => `
                <div class="org-member-item">
                    <div class="org-member-avatar">
                        ${m.avatar_url ? `<img src="${m.avatar_url}" alt="">` : '👤'}
                    </div>
                    <div class="org-member-info">
                        <div class="org-member-name">${escapeHtml(m.name || m.email)}</div>
                        <div class="org-member-email">${escapeHtml(m.email)}</div>
                    </div>
                    <span class="org-member-role ${m.role}">${m.role}</span>
                </div>
            `).join('');
        }
        
        if (notebooksList && org.notebooks) {
            notebooksList.innerHTML = org.notebooks.length ? org.notebooks.map(nb => `
                <div class="org-notebook-item" data-nb-id="${nb.id}">
                    <span class="org-notebook-name">📒 ${escapeHtml(nb.name)}</span>
                    <span class="org-notebook-date">${new Date(nb.created_at).toLocaleDateString()}</span>
                </div>
            `).join('') : '<p class="org-empty-msg">No shared notebooks yet</p>';
        }
        
        if (orgsDetailPanel) orgsDetailPanel.style.display = 'flex';
        if (orgCreateForm) orgCreateForm.style.display = 'none';
        if (orgInviteForm) orgInviteForm.style.display = 'none';
    }
    
    async function createOrganization() {
        const nameInput = document.getElementById('org-name-input');
        const slugInput = document.getElementById('org-slug-input');
        const descInput = document.getElementById('org-desc-input');
        
        if (!nameInput?.value?.trim()) {
            showToast('Please enter a team name', 'error');
            return;
        }
        
        try {
            const resp = await fetch(`${API}/organizations`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(window.currentUser ? { 'X-User-Id': window.currentUser.id } : {})
                },
                body: JSON.stringify({
                    name: nameInput.value.trim(),
                    slug: slugInput?.value?.trim() || nameInput.value.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
                    description: descInput?.value?.trim() || null
                })
            });
            
            if (resp.ok) {
                const data = await resp.json();
                showToast('Organization created!', 'success');
                await loadOrganizations();
                loadOrgDetails(data.id);
                hideOrgCreateForm();
            } else {
                const err = await resp.json();
                showToast(err.detail || 'Failed to create organization', 'error');
            }
        } catch (e) {
            showToast('Failed to create organization', 'error');
        }
    }
    
    async function sendInvite() {
        const emailInput = document.getElementById('invite-email-input');
        const roleSelect = document.getElementById('invite-role-select');
        
        if (!emailInput?.value?.trim() || !currentOrgId) {
            showToast('Please enter an email', 'error');
            return;
        }
        
        try {
            const resp = await fetch(`${API}/organizations/${currentOrgId}/invite`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(window.currentUser ? { 'X-User-Id': window.currentUser.id } : {})
                },
                body: JSON.stringify({
                    email: emailInput.value.trim(),
                    role: roleSelect?.value || 'member'
                })
            });
            
            if (resp.ok) {
                showToast('Invite sent!', 'success');
                hideOrgInviteForm();
                emailInput.value = '';
            } else {
                const err = await resp.json();
                showToast(err.detail || 'Failed to send invite', 'error');
            }
        } catch (e) {
            showToast('Failed to send invite', 'error');
        }
    }
    
    function showOrgsModal() {
        loadOrganizations();
        orgsModalOverlay?.classList.add('show');
    }
    
    function hideOrgsModal() {
        orgsModalOverlay?.classList.remove('show');
        currentOrgId = null;
    }
    
    function showOrgCreateForm() {
        if (orgCreateForm) orgCreateForm.style.display = 'block';
        if (orgsDetailPanel) orgsDetailPanel.style.display = 'none';
        if (orgInviteForm) orgInviteForm.style.display = 'none';
    }
    
    function hideOrgCreateForm() {
        if (orgCreateForm) orgCreateForm.style.display = 'none';
        document.getElementById('org-name-input')?.value && (document.getElementById('org-name-input').value = '');
        document.getElementById('org-slug-input')?.value && (document.getElementById('org-slug-input').value = '');
        document.getElementById('org-desc-input')?.value && (document.getElementById('org-desc-input').value = '');
    }
    
    function showOrgInviteForm() {
        if (orgInviteForm) orgInviteForm.style.display = 'block';
        if (orgCreateForm) orgCreateForm.style.display = 'none';
    }
    
    function hideOrgInviteForm() {
        if (orgInviteForm) orgInviteForm.style.display = 'none';
    }
    
    function setupOrgTabs() {
        document.querySelectorAll('.org-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.org-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                
                const tabName = tab.dataset.tab;
                document.querySelectorAll('.org-tab-panel').forEach(p => p.style.display = 'none');
                document.getElementById(`org-${tabName}-panel`)?.style && (document.getElementById(`org-${tabName}-panel`).style.display = 'block');
                
                if (tabName === 'activity' && currentOrgId) {
                    loadOrgActivity(currentOrgId);
                }
            });
        });
    }
    
    async function loadOrgActivity(orgId) {
        const activityList = document.getElementById('org-activity-list');
        if (!activityList) return;
        
        try {
            const resp = await fetch(`${API}/activity?org_id=${orgId}`);
            if (resp.ok) {
                const data = await resp.json();
                if (data.activities?.length) {
                    activityList.innerHTML = data.activities.map(a => `
                        <div class="activity-item">
                            <span class="activity-user">${escapeHtml(a.user_name || 'Someone')}</span>
                            <span class="activity-action">${a.action}</span>
                            ${a.target_title ? `<span class="activity-target">${escapeHtml(a.target_title)}</span>` : ''}
                            <span class="activity-time">${formatTimeAgo(a.created_at)}</span>
                        </div>
                    `).join('');
                } else {
                    activityList.innerHTML = '<p class="activity-empty">No recent activity</p>';
                }
            }
        } catch (e) {
            console.warn('Failed to load activity:', e);
        }
    }
    
    function formatTimeAgo(dateStr) {
        const date = new Date(dateStr);
        const now = new Date();
        const diff = Math.floor((now - date) / 1000);
        
        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
        return date.toLocaleDateString();
    }

    // ══════════════════════════════════════════════════════════════════════
    //   COMMENTS
    // ══════════════════════════════════════════════════════════════════════
    
    async function loadComments(notebookId, sourceId = null, noteId = null) {
        let url = `${API}/comments?`;
        if (notebookId) url += `notebook_id=${notebookId}`;
        if (sourceId) url += `&source_id=${sourceId}`;
        if (noteId) url += `&note_id=${noteId}`;
        
        try {
            const resp = await fetch(url);
            if (resp.ok) {
                const data = await resp.json();
                return data.comments || [];
            }
        } catch (e) {
            console.warn('Failed to load comments:', e);
        }
        return [];
    }
    
    async function addComment(content, notebookId, sourceId = null, noteId = null, parentId = null) {
        if (!window.currentUser) {
            showToast('Please sign in to comment', 'error');
            return null;
        }
        
        try {
            const resp = await fetch(`${API}/comments`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Id': window.currentUser.id
                },
                body: JSON.stringify({
                    content,
                    notebook_id: notebookId,
                    source_id: sourceId,
                    note_id: noteId,
                    parent_id: parentId
                })
            });
            
            if (resp.ok) {
                const data = await resp.json();
                showToast('Comment added', 'success');
                return data;
            }
        } catch (e) {
            showToast('Failed to add comment', 'error');
        }
        return null;
    }
    
    // ══════════════════════════════════════════════════════════════════════
    //   EVENT LISTENERS
    // ══════════════════════════════════════════════════════════════════════
    
    function init() {
        nbTagsBtn?.addEventListener('click', () => showTagsModal());
        tagsModalClose?.addEventListener('click', hideTagsModal);
        tagsModalOverlay?.addEventListener('click', e => { if (e.target === tagsModalOverlay) hideTagsModal(); });
        tagCreateBtn?.addEventListener('click', createTag);
        tagNameInput?.addEventListener('keydown', e => { if (e.key === 'Enter') createTag(); });
        
        nbLinksBtn?.addEventListener('click', showLinksModal);
        linksModalClose?.addEventListener('click', hideLinksModal);
        linksModalOverlay?.addEventListener('click', e => { if (e.target === linksModalOverlay) hideLinksModal(); });
        linkCreateBtn?.addEventListener('click', showLinkCreateForm);
        linkCancelBtn?.addEventListener('click', hideLinkCreateForm);
        linkSaveBtn?.addEventListener('click', saveLink);
        linkTypeFilter?.addEventListener('change', loadLinks);
        
        analyticsBtn?.addEventListener('click', showAnalyticsModal);
        analyticsModalClose?.addEventListener('click', hideAnalyticsModal);
        analyticsModalOverlay?.addEventListener('click', e => { if (e.target === analyticsModalOverlay) hideAnalyticsModal(); });
        
        document.querySelectorAll('.analytics-period-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.analytics-period-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                loadAnalytics(parseInt(btn.dataset.days));
            });
        });
        
        calSyncBtn?.addEventListener('click', showCalendarSyncModal);
        calendarSyncClose?.addEventListener('click', hideCalendarSyncModal);
        calendarSyncOverlay?.addEventListener('click', e => { if (e.target === calendarSyncOverlay) hideCalendarSyncModal(); });
        
        document.querySelectorAll('.calendar-provider-btn').forEach(btn => {
            btn.addEventListener('click', () => connectCalendar(btn.dataset.provider));
        });
        
        calDeadlineBtn?.addEventListener('click', () => showDeadlineModal());
        deadlineModalClose?.addEventListener('click', hideDeadlineModal);
        deadlineCancelBtn?.addEventListener('click', hideDeadlineModal);
        deadlineSaveBtn?.addEventListener('click', saveDeadline);
        deadlineModalOverlay?.addEventListener('click', e => { if (e.target === deadlineModalOverlay) hideDeadlineModal(); });
        
        paperVersionsBtn?.addEventListener('click', showVersionsModal);
        versionsModalClose?.addEventListener('click', hideVersionsModal);
        versionsRestoreBtn?.addEventListener('click', restoreVersion);
        versionsModalOverlay?.addEventListener('click', e => { if (e.target === versionsModalOverlay) hideVersionsModal(); });
        
        organizationsBtn?.addEventListener('click', showOrgsModal);
        orgsModalClose?.addEventListener('click', hideOrgsModal);
        orgsModalOverlay?.addEventListener('click', e => { if (e.target === orgsModalOverlay) hideOrgsModal(); });
        document.getElementById('org-create-btn')?.addEventListener('click', showOrgCreateForm);
        document.getElementById('org-cancel-btn')?.addEventListener('click', hideOrgCreateForm);
        document.getElementById('org-save-btn')?.addEventListener('click', createOrganization);
        document.getElementById('org-invite-btn')?.addEventListener('click', showOrgInviteForm);
        document.getElementById('invite-cancel-btn')?.addEventListener('click', hideOrgInviteForm);
        document.getElementById('invite-send-btn')?.addEventListener('click', sendInvite);
        setupOrgTabs();
        
        setupAutoVersionSave();
    }
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    
    return {
        showTagsModal,
        showLinksModal,
        showAnalyticsModal,
        showDeadlineModal,
        showVersionsModal,
        showOrgsModal,
        loadTags,
        loadLinks,
        loadDeadlines,
        loadOrganizations,
        saveVersion,
        loadComments,
        addComment
    };
})();
