// ── Import ────────────────────────────────────────────────────────────────────
let importFile = null;
let importPreviewData = null;
let activeImportId = null;
let importCancelRequested = false;

function handleDragOver(e) {
  e.preventDefault();
  document.getElementById('dropZone').style.borderColor = 'var(--accent)';
  document.getElementById('dropZone').style.background = 'rgba(232,197,71,0.05)';
}

function handleDragLeave(e) {
  document.getElementById('dropZone').style.borderColor = 'var(--border)';
  document.getElementById('dropZone').style.background = 'var(--surface2)';
}

function handleDrop(e) {
  e.preventDefault();
  handleDragLeave(e);
  const file = e.dataTransfer.files[0];
  if (file) processImportFile(file);
}

function handleFileSelect(e) {
  const file = e.target.files[0];
  if (file) processImportFile(file);
  e.target.value = '';
}

async function processImportFile(file) {
  importFile = file;
  showStatus('importStatus', t('js.processingFile', file.name), 'info');

  const fd = new FormData();
  fd.append('file', file);

  try {
    const r = await fetch(`${API}/import/preview`, { method: 'POST', body: fd });
    const contentType = r.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      showStatus('importStatus', t('js.backendError', r.status), 'error');
      return;
    }
    const d = await r.json();
    if (!r.ok) {
      showStatus('importStatus', t('js.error', d.error), 'error');
      return;
    }
    importPreviewData = d;
    showImportPreview(d, file.name);
  } catch(e) {
    showStatus('importStatus', t('js.connectionError', e.message), 'error');
  }
}

function showImportPreview(d, fname) {
  document.getElementById('importStep2').style.display = 'block';
  document.getElementById('previewTitle').textContent =
    t('js.previewTitle', fname, d.total_rows);

  // Column mapping summary
  const mapEl = document.getElementById('columnMapping');
  const mapped = d.mapped_columns || [];
  const unknown = d.unknown_columns || [];
  mapEl.innerHTML = `
    <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:6px;">
      ${mapped.map(c => `<span class="tag" style="color:var(--success);border-color:rgba(64,192,128,.3);background:rgba(64,192,128,.07)">✓ ${c}</span>`).join('')}
      ${unknown.map(c => `<span class="tag" style="color:var(--text-muted)">~ ${c}</span>`).join('')}
    </div>
    <div style="font-size:0.75rem; color:var(--text-muted);">
      <span style="color:var(--success)">${t('js.columnsRecognized', mapped.length)}</span>
      ${unknown.length ? ` · <span>${t('js.columnsUnknown', unknown.length)}</span>` : ''}
    </div>`;

  // Preview table
  const preview = d.preview || [];
  const cols = d.detected_columns || [];
  const knownCols = cols.filter(c => d.mapped_columns.includes(c));
  const tableCols = knownCols.length > 0 ? knownCols : cols.slice(0, 8);

  const table = document.getElementById('previewTable');
  table.innerHTML = `
    <thead>
      <tr style="background:var(--surface2); border-bottom:1px solid var(--border);">
        ${tableCols.map(c => `<th style="padding:8px 12px; text-align:left; color:${d.mapped_columns.includes(c) ? 'var(--accent)' : 'var(--text-muted)'}; white-space:nowrap;">${c}</th>`).join('')}
      </tr>
    </thead>
    <tbody>
      ${preview.map((row, i) => `
        <tr style="border-bottom:1px solid var(--border); background:${i%2===0?'transparent':'rgba(255,255,255,.02)'}">
          ${tableCols.map(c => `<td style="padding:7px 12px; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text);" title="${(row[c]||'').replace(/"/g,'&quot;')}">${row[c] || '<span style=color:var(--border)>—</span>'}</td>`).join('')}
        </tr>`).join('')}
    </tbody>`;

  document.getElementById('previewCount').textContent =
    t('js.showingRows', preview.length, d.total_rows);

  document.getElementById('importStatus').className = 'status-msg';
  const cancelBtn = document.getElementById('btnCancelImport');
  cancelBtn.disabled = true;
}

async function doImport() {
  if (!importFile) return;
  const btn = document.getElementById('btnDoImport');
  const cancelBtn = document.getElementById('btnCancelImport');
  btn.innerHTML = t('js.importing');
  btn.disabled = true;
  cancelBtn.disabled = false;

  activeImportId = (window.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : (`imp_${Date.now()}_${Math.random().toString(16).slice(2)}`);
  importCancelRequested = false;

  // Show progress container
  const progressContainer = document.createElement('div');
  progressContainer.id = 'importProgressContainer';
  progressContainer.style.cssText = `
    margin-top: 16px;
    padding: 16px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
  `;
  progressContainer.innerHTML = `
    <div style="font-size:0.85rem; margin-bottom:8px; color:var(--text-muted);">
      <span id="importProgressText">${t('js.importProgress', 0, 0, 0, 0, 0, 0)}</span>
    </div>
    <div style="background:var(--surface); border-radius:4px; height:24px; overflow:hidden; position:relative;">
      <div id="importProgressBar" style="
        height:100%; width:0%;
        background:linear-gradient(90deg, var(--accent), var(--accent2));
        transition:width 0.3s ease;
        display:flex; align-items:center; justify-content:center;
        font-size:0.7rem; color:#000; font-weight:500;
      "></div>
    </div>
  `;
  document.getElementById('importStep2').appendChild(progressContainer);

  const fd = new FormData();
  fd.append('file', importFile);
  fd.append('mode', document.getElementById('importMode').value);
  fd.append('fetch_posters', 'false');  // posters worden in de enrich-stap opgehaald
  fd.append('enrich', document.getElementById('importEnrich').checked ? 'true' : 'false');
  fd.append('import_id', activeImportId);

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 1800000);

    const r = await fetch(`${API}/import`, { method: 'POST', body: fd, signal: controller.signal });
    clearTimeout(timeoutId);

    if (!r.ok) {
      showStatus('importStatus', t('js.backendError', r.status), 'error');
      btn.innerHTML = t('js.importBtn');
      btn.disabled = false;
      cancelBtn.disabled = true;
      progressContainer.remove();
      activeImportId = null;
      importCancelRequested = false;
      return;
    }

    // Parse streaming JSON responses
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalResult = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // Keep incomplete line in buffer

      for (const line of lines) {
        if (!line) continue;
        try {
          const d = JSON.parse(line);
          if (d.type === 'progress') {
            const pct = d.percent || 0;
            document.getElementById('importProgressBar').style.width = pct + '%';
            document.getElementById('importProgressBar').textContent = pct + '%';
            document.getElementById('importProgressText').textContent = 
              t('js.importProgress', d.current, d.total, d.added, d.updated, d.skipped, d.errors);
          } else if (d.type === 'done') {
            finalResult = d;
          }
        } catch(e) {}
      }
    }

    // Handle final result
    if (!finalResult) {
      showStatus('importStatus', t('js.errorShort'), 'error');
      btn.innerHTML = t('js.importBtn');
      btn.disabled = false;
      cancelBtn.disabled = true;
      progressContainer.remove();
      activeImportId = null;
      importCancelRequested = false;
      return;
    }

    const d = finalResult;

    progressContainer.remove();
    cancelBtn.disabled = true;
    activeImportId = null;
    importCancelRequested = false;
    document.getElementById('importStep2').style.display = 'none';
    document.getElementById('importStep3').style.display = 'block';

    const resultEl = document.getElementById('importResult');
    const hasErrors = d.errors > 0;
    const isCancelled = !!d.cancelled;
    resultEl.innerHTML = `
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:16px; margin-bottom:20px;">
        <div style="background:rgba(64,192,128,.08); border:1px solid rgba(64,192,128,.25); border-radius:8px; padding:16px; text-align:center;">
          <div style="font-family:'DM Serif Display',serif; font-size:2rem; color:var(--success);">${d.added}</div>
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em;">${t('js.importAdded')}</div>
        </div>
        <div style="background:rgba(124,106,247,.08); border:1px solid rgba(124,106,247,.25); border-radius:8px; padding:16px; text-align:center;">
          <div style="font-family:'DM Serif Display',serif; font-size:2rem; color:#a09af8;">${d.updated}</div>
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em;">${t('js.importUpdated')}</div>
        </div>
        <div style="background:var(--surface2); border:1px solid var(--border); border-radius:8px; padding:16px; text-align:center;">
          <div style="font-family:'DM Serif Display',serif; font-size:2rem; color:var(--text-muted);">${d.skipped}</div>
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em;">${t('js.importSkipped')}</div>
        </div>
        ${hasErrors ? `<div style="background:rgba(240,64,96,.08); border:1px solid rgba(240,64,96,.25); border-radius:8px; padding:16px; text-align:center;">
          <div style="font-family:'DM Serif Display',serif; font-size:2rem; color:var(--danger);">${d.errors}</div>
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em;">${t('js.importErrors')}</div>
        </div>` : ''}
      </div>
      ${isCancelled ? `<div style="margin-bottom:12px; font-size:0.84rem; color:var(--accent);">${t('js.importCancelled')}</div>` : ''}
      ${hasErrors && d.error_details?.length ? `
        <div style="background:rgba(240,64,96,.06); border:1px solid rgba(240,64,96,.2); border-radius:6px; padding:14px; font-size:0.78rem; font-family:'DM Mono',monospace; color:var(--danger);">
          ${d.error_details.map(e => `<div>${e}</div>`).join('')}
        </div>` : ''}`;

    // Clear stale file reference
    importFile = null;
    document.getElementById('fileInput').value = '';

    // Reload collection, then start per-movie enrichment
    await loadCollection();
    loadStats();

    if (!isCancelled && d.added > 0) {
      await postImportEnrich();
    } else {
      document.getElementById('btnViewCollection').style.display = 'inline-flex';
    }

  } catch(e) {
    cancelBtn.disabled = true;
    activeImportId = null;
    importCancelRequested = false;
    importFile = null;
    document.getElementById('fileInput').value = '';
    showStatus('importStatus', t('js.error', e.message), 'error');
    btn.innerHTML = t('js.importBtn');
    btn.disabled = false;
  }
}

async function cancelImportRun() {
  if (!activeImportId) return;
  const btn = document.getElementById('btnCancelImport');
  btn.disabled = true;
  importCancelRequested = true;
  showStatus('importStatus', t('js.cancellingImport'), 'info');
  try {
    const r = await fetch(`${API}/import/cancel/${encodeURIComponent(activeImportId)}`, { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      showStatus('importStatus', d.error || t('js.cancelFailed'), 'error');
      btn.disabled = false;
      importCancelRequested = false;
      return;
    }
    showStatus('importStatus', t('js.cancelSent'), 'info');
  } catch(e) {
    showStatus('importStatus', t('js.cancelFailed') + ': ' + e.message, 'error');
    btn.disabled = false;
    importCancelRequested = false;
  }
}

async function postImportEnrich() {
  const section  = document.getElementById('postImportEnrich');
  const bar      = document.getElementById('enrichBar');
  const counter  = document.getElementById('enrichCounter');
  const log      = document.getElementById('enrichLog');
  const title    = document.getElementById('enrichTitle');
  const resultEl = document.getElementById('enrichResult');

  section.style.display = 'block';
  log.innerHTML = '';

  // Get all movies (freshly imported)
  let movies = [];
  try {
    const r = await fetch(`${API}/movies`);
    movies = await r.json();
  } catch(e) { return; }

  const total = movies.length;
  let enriched = 0, skippedCount = 0, errorCount = 0;
  title.textContent = t('js.enrichTitle', total);

  for (let i = 0; i < movies.length; i++) {
    const m = movies[i];
    const pct = Math.round(((i + 1) / total) * 100);
    bar.style.width = pct + '%';
    counter.textContent = `${i + 1} / ${total}`;
    title.textContent = t('js.enrichProgress', i + 1, total);

    try {
      const r = await fetch(`${API}/movies/${m.id}/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fetch_posters: true })
      });
      const d = await r.json();

      if (d.status === 'updated') {
        enriched++;
        const flds = d.fields?.length ? d.fields.join(', ') : '';
        const posterIcon = d.has_poster ? ' 🖼' : '';
        log.innerHTML += `<div style="color:var(--success)">✓ ${d.title} — ${d.source}${posterIcon}</div>`;
      } else if (d.status === 'skipped') {
        skippedCount++;
        log.innerHTML += `<div style="color:var(--text-muted)">— ${m.title} (${t('js.enrichNotFound')})</div>`;
      } else if (d.status === 'error') {
        errorCount++;
        log.innerHTML += `<div style="color:var(--danger)">✕ ${m.title}: ${d.error}</div>`;
      }
    } catch(e) {
      errorCount++;
      log.innerHTML += `<div style="color:var(--danger)">✕ ${m.title}: ${e.message}</div>`;
    }

    // Auto-scroll log
    log.scrollTop = log.scrollHeight;
  }

  bar.style.width = '100%';
  title.textContent = t('js.enrichDone');

  resultEl.style.display = 'block';
  resultEl.innerHTML = `
    <div style="display:flex; gap:16px; flex-wrap:wrap;">
      <div style="font-size:0.88rem;"><span style="color:var(--success); font-weight:500;">${enriched}</span> ${t('js.enrichUpdated')}</div>
      <div style="font-size:0.88rem;"><span style="color:var(--text-muted); font-weight:500;">${skippedCount}</span> ${t('js.enrichNotFound')}</div>
      ${errorCount > 0 ? `<div style="font-size:0.88rem;"><span style="color:var(--danger); font-weight:500;">${errorCount}</span> ${t('js.enrichErrors')}</div>` : ''}
    </div>`;

  document.getElementById('btnViewCollection').style.display = 'inline-flex';

  // Reload collection to reflect new posters/data
  await loadCollection();
  loadStats();
}

function resetImport() {
  importFile = null;
  importPreviewData = null;
  activeImportId = null;
  importCancelRequested = false;
  document.getElementById('fileInput').value = '';
  document.getElementById('importStep2').style.display = 'none';
  document.getElementById('importStep3').style.display = 'none';
  document.getElementById('postImportEnrich').style.display = 'none';
  document.getElementById('enrichLog').innerHTML = '';
  document.getElementById('enrichResult').style.display = 'none';
  document.getElementById('enrichBar').style.width = '0%';
  document.getElementById('btnViewCollection').style.display = 'none';
  document.getElementById('btnDoImport').innerHTML = t('js.importBtn');
  document.getElementById('btnDoImport').disabled = false;
  document.getElementById('btnCancelImport').disabled = true;
  document.getElementById('importStatus').className = 'status-msg';
}

function switchTabDirect(name) {
  const activeTabName = (name === 'logs' || name === 'settings' || name === 'toevoegen'
    || name === 'scan' || name === 'import') ? 'meer'
    : name;
  const panelName = (name === 'scan' || name === 'import') ? 'toevoegen'
    : name;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const selected = document.querySelector(`.tab[data-tab="${activeTabName}"]`);
  if (selected) selected.classList.add('active');
  const panel = document.getElementById(`panel-${panelName}`);
  if (panel) panel.classList.add('active');
  if (isMobileNav()) closeMobileMenu();
  if (panelName === 'collection') loadCollection();
  if (panelName === 'lists') switchListsSubmenu(currentListsSub || 'watchlist');
  if (name === 'logs') loadLogs();
  if (name === 'settings') loadSettings();
  if (name === 'admin')    loadAdminTab();
  if (name === 'profile') switchProfileSubmenu(currentProfileSubmenu || 'general');
  if (name === 'import') switchToevoegen('import');
  if (name === 'search') {
    filterSearchMovies();
    const input = document.getElementById('searchPageInput');
    if (input) {
      setTimeout(() => input.focus(), 0);
    }
  }
}

function openLogsFromSettings() {
  switchTab('admin');
}

// ── Client-side Routing ───────────────────────────────────────────────────────
const _TAB_PATHS = { collection:'/', settings:'/settings', search:'/search', lists:'/lists', toevoegen:'/add', profile:'/profile', admin:'/admin' };
const _PATH_TABS = { '/':'collection', '/settings':'settings', '/search':'search', '/lists':'lists', '/add':'toevoegen', '/profile':'profile', '/admin':'admin' };

function _tabPath(tab) {
  const base = (tab === 'logs') ? 'settings' : (tab === 'scan' || tab === 'import') ? 'toevoegen' : tab;
  return _TAB_PATHS[base] || '/';
}
function _pushRoute(path) {
  if (window.location.pathname !== path) history.pushState({ path }, '', path);
}
function _replaceRoute(path) {
  history.replaceState({ path }, '', path);
}

/* ── Deep-link resolvers for group routes ────────────────────────────────── */
// Find the aggregated collection card in allMovies and open it.
function _openCollectionRoute(id) {
  switchTabDirect('collection');
  const tryOpen = (tries) => {
    const card = allMovies.find(m => m._is_collection && m._collection_id === id);
    if (card) { openCollectionView(card); }
    else if (tries > 0) { setTimeout(() => tryOpen(tries - 1), 300); }
    else { _replaceRoute('/'); }
  };
  tryOpen(15);
}
// Find the aggregated box-set card and open it.
function _openBoxSetRoute(id) {
  switchTabDirect('collection');
  const tryOpen = (tries) => {
    const card = allMovies.find(m => m._is_super_group && !m._is_collection && m._parent_group_id === id);
    if (card) { openSuperGroupView(card); }
    else if (tries > 0) { setTimeout(() => tryOpen(tries - 1), 300); }
    else { _replaceRoute('/'); }
  };
  tryOpen(15);
}
// Open a vault by edition_group id (uses primary movie lookup).
function _openVaultRoute(id) {
  switchTabDirect('collection');
  const tryOpen = (tries) => {
    const primary = allMovies.find(m => m.edition_group_id === id && !m._isNested);
    if (primary) { openEditionGroupView(primary.id, primary); }
    else if (tries > 0) { setTimeout(() => tryOpen(tries - 1), 300); }
    else { _replaceRoute('/'); }
  };
  tryOpen(15);
}

function _handleRoute() {
  // Handle hash-based deep links (e.g. from push notification clicks).
  if (window.location.hash === '#invites') {
    history.replaceState(null, '', window.location.pathname);
    openInvitePanel();
    return;
  }
  const path = window.location.pathname;
  const personMatch = path.match(/^\/person\/(\d+)$/);
  if (personMatch) {
    openPersonDetail(parseInt(personMatch[1], 10));
    return;
  }
  const movieMatch = path.match(/^\/movie\/(\d+)$/);
  if (movieMatch) {
    const id = parseInt(movieMatch[1], 10);
    switchTabDirect('collection');
    const tryOpen = (tries) => {
      const found = allMovies.find(m => m.id === id);
      if (found) { openMovieDetail(id); }
      else if (tries > 0) { setTimeout(() => tryOpen(tries - 1), 300); }
      else { _replaceRoute('/'); }
    };
    tryOpen(15);
    return;
  }
  const collectionMatch = path.match(/^\/collection\/(\d+)$/);
  if (collectionMatch) { _openCollectionRoute(parseInt(collectionMatch[1], 10)); return; }
  const boxsetMatch = path.match(/^\/boxset\/(\d+)$/);
  if (boxsetMatch) { _openBoxSetRoute(parseInt(boxsetMatch[1], 10)); return; }
  const vaultMatch = path.match(/^\/vault\/(\d+)$/);
  if (vaultMatch) { _openVaultRoute(parseInt(vaultMatch[1], 10)); return; }
  const tab = _PATH_TABS[path];
  if (tab) switchTabDirect(tab);
  else _replaceRoute('/');
}

window.addEventListener('popstate', (e) => {
  const path = (e.state && e.state.path) || window.location.pathname;
  const personMatch = path.match(/^\/person\/(\d+)$/);
  if (personMatch) { openPersonDetail(parseInt(personMatch[1], 10)); return; }
  const movieMatch = path.match(/^\/movie\/(\d+)$/);
  if (movieMatch) { openMovieDetail(parseInt(movieMatch[1], 10)); return; }
  const collectionMatch = path.match(/^\/collection\/(\d+)$/);
  if (collectionMatch) { _openCollectionRoute(parseInt(collectionMatch[1], 10)); return; }
  const boxsetMatch = path.match(/^\/boxset\/(\d+)$/);
  if (boxsetMatch) { _openBoxSetRoute(parseInt(boxsetMatch[1], 10)); return; }
  const vaultMatch = path.match(/^\/vault\/(\d+)$/);
  if (vaultMatch) { _openVaultRoute(parseInt(vaultMatch[1], 10)); return; }
  const tab = _PATH_TABS[path] || 'collection';
  switchTabDirect(tab);
});

window.addEventListener('resize', () => {
  if (!isMobileNav()) closeMobileMenu();
});

window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const active = document.querySelector('.panel.active');
    if (active && active.id === 'panel-person-detail') { closePersonDetail(); return; }
    if (active && active.id === 'panel-movie-detail') { closeMovieDetail(); return; }
    closeMobileMenu();
  }
});

window.addEventListener('scroll', updateScrollState, { passive: true });
updateScrollState();
window.addEventListener('online', () => {
  updateConnectionState();
  flushQueuedMutations();
});
window.addEventListener('offline', updateConnectionState);
updateConnectionState();
updateQueueIndicator();

// ── Logs ──────────────────────────────────────────────────────────────────────
let logAutoTimer = null;

async function loadLogs() {
  const level = document.getElementById('logLevelFilter').value;
  const cat   = document.getElementById('logCatFilter').value;
  const params = new URLSearchParams({ limit: '300' });
  if (level) params.set('level', level);
  if (cat)   params.set('category', cat);

  try {
    const r = await fetch(`${API}/logs?${params}`);
    const logs = await r.json();
    renderLogs(logs);
  } catch(e) {
    document.getElementById('logBody').innerHTML =
      `<tr><td colspan="5" style="text-align:center;padding:30px;color:var(--danger)">${t('js.logsLoadError', e.message)}</td></tr>`;
  }
}

function renderLogs(logs) {
  const body = document.getElementById('logBody');
  document.getElementById('logCount').textContent = t('js.logCount', logs.length);

  if (!logs.length) {
    body.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:40px; color:var(--text-muted);">${t('js.noLogs')}</td></tr>`;
    return;
  }

  const esc = (s) => String(s || '').replace(/</g, '&lt;');
  const allowedStatuses = new Set(['hit', 'miss', 'skipped', 'error', 'partial']);

  function renderBackendTrace(traceText) {
    if (!traceText) {
      return '<div class="log-backend-trace" style="color:var(--text-muted)">-</div>';
    }
    const parts = traceText.split(' | ').map(p => p.trim()).filter(Boolean);
    if (!parts.length) {
      return '<div class="log-backend-trace" style="color:var(--text-muted)">-</div>';
    }
    const chips = parts.map(part => {
      const m = part.match(/^([^:]+):\s*(hit|miss|skipped|error|partial)\b(.*)$/i);
      if (!m) {
        return `<span class="backend-chip skipped">${esc(part)}</span>`;
      }
      const backend = (m[1] || '').trim();
      const status = (m[2] || 'skipped').toLowerCase();
      const rest = (m[3] || '').trim();
      const cls = allowedStatuses.has(status) ? status : 'skipped';
      const label = `${backend}: ${status}${rest ? ' ' + rest : ''}`;
      return `<span class="backend-chip ${cls}">${esc(label)}</span>`;
    }).join('');
    return `<div class="log-backend-trace">${chips}</div>`;
  }

  body.innerHTML = logs.map(l => {
    const ts = (l.timestamp || '').replace('T', ' ').slice(0, 19);
    const rawDetail = String(l.detail || '');
    const marker = 'Backends:';
    const idx = rawDetail.indexOf(marker);
    const backendTrace = idx >= 0 ? rawDetail.slice(idx + marker.length).trim() : '';
    const detailWithoutBackend = idx >= 0 ? rawDetail.slice(0, idx).trim() : rawDetail;
    const backendHtml = renderBackendTrace(backendTrace);
    const detailHtml = detailWithoutBackend
      ? `<div class="log-detail">${esc(detailWithoutBackend)}</div>`
      : '';

    return `<tr>
      <td class="ts" data-label="${t('logs.thTimestamp')}">${ts}</td>
      <td data-label="${t('logs.thLevel')}"><span class="log-level ${l.level}">${l.level}</span></td>
      <td class="log-cat" data-label="${t('logs.thCategory')}">${l.category}</td>
      <td data-label="${t('logs.thBackends')}">${backendHtml}</td>
      <td data-label="${t('logs.thMessage')}">
        <div class="log-msg">${esc(l.message)}</div>
        ${detailHtml}
      </td>
    </tr>`;
  }).join('');
}

async function clearLogs() {
  if (!confirm(t('js.confirmClearLogs'))) return;
  await fetch(`${API}/logs`, { method: 'DELETE' });
  loadLogs();
}

// Auto-refresh timer
function startLogAutoRefresh() {
  stopLogAutoRefresh();
  logAutoTimer = setInterval(() => {
    const panel = document.getElementById('panel-admin');
    const logsSection = document.getElementById('adminSubLogs');
    const cb    = document.getElementById('logAutoRefresh');
    if (panel && panel.classList.contains('active') && logsSection && logsSection.classList.contains('active') && cb && cb.checked) {
      loadLogs();
    }
  }, 4000);
}

function stopLogAutoRefresh() {
  if (logAutoTimer) { clearInterval(logAutoTimer); logAutoTimer = null; }
}

startLogAutoRefresh();

