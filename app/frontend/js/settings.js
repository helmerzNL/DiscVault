// ── Backup / Restore / Reset ──────────────────────────────────────────────────

async function loadSettings() {
  loadDbStats();
  loadBackups();
  loadAuthSettings();
  loadQueueSettings();
  loadSourceSettings();

  // Check admin status and show/hide restricted tabs
  try {
    const mr = await fetch(`${API}/auth/me`);
    const me = await mr.json();
    const isSettingsAdmin = !me.authenticated || me.role === 'admin';
    const logsBtn   = document.querySelector('[data-settings-sub="logs"]');
    const advBtn    = document.querySelector('[data-settings-sub="advanced"]');
    const backupBtn = document.querySelector('[data-settings-sub="backup"]');
    if (logsBtn)   logsBtn.style.display   = isSettingsAdmin ? '' : 'none';
    if (advBtn)    advBtn.style.display    = isSettingsAdmin ? '' : 'none';
    if (backupBtn) backupBtn.style.display = isSettingsAdmin ? '' : 'none';
    if (isSettingsAdmin) {
      loadDebugSettings();
      loadMcpSettings();
    }
    // If non-admin is on a restricted tab, redirect to general
    if (!isSettingsAdmin && (currentSettingsSubmenu === 'logs' || currentSettingsSubmenu === 'advanced' || currentSettingsSubmenu === 'backup')) {
      currentSettingsSubmenu = 'general';
    }
  } catch(e) {
    loadDebugSettings();
    loadMcpSettings();
  }
  switchSettingsSubmenu(currentSettingsSubmenu);
}

let currentSettingsSubmenu = 'profile';

function switchSettingsSubmenu(name) {
  currentSettingsSubmenu = name;
  document.querySelectorAll('[data-settings-sub]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-settings-sub') === name);
  });
  const map = {
    profile: 'settingsSubProfile',
    general: 'settingsSubGeneral',
    security: 'settingsSubSecurity',
    backup: 'settingsSubBackup',
    logs: 'settingsSubLogs',
    advanced: 'settingsSubAdvanced',
    notifications: 'settingsSubNotifications',
  };
  Object.values(map).forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
  });
  const target = document.getElementById(map[name] || map.profile);
  if (target) target.classList.add('active');
  if (name === 'profile') { loadProfile(); loadApiKeys(); loadMcpLogs(); }
  if (name === 'logs') loadLogs();
  if (name === 'notifications') initPushNotifications();
}

async function loadSourceSettings() {
  try {
    const r = await fetch(`${API}/settings/sources`);
    const d = await r.json();
    const elOmdb = document.getElementById('sourceOmdbToggle');
    const elTmdb = document.getElementById('sourceTmdbToggle');
    const el = document.getElementById('sourceBlurayToggle');
    const el2 = document.getElementById('sourceBlurayDiscDeToggle');
    if (elOmdb) elOmdb.checked = !!d.omdb_enabled;
    if (elTmdb) elTmdb.checked = !!d.tmdb_enabled;
    if (el) el.checked = !!d.bluray_scrape_enabled;
    if (el2) el2.checked = !!d.bluraydiscde_scrape_enabled;
  } catch(e) {}
}

async function saveSourceSettings() {
  const elOmdb = document.getElementById('sourceOmdbToggle');
  const elTmdb = document.getElementById('sourceTmdbToggle');
  const el = document.getElementById('sourceBlurayToggle');
  const el2 = document.getElementById('sourceBlurayDiscDeToggle');
  const body = {
    omdb_enabled: !!(elOmdb && elOmdb.checked),
    tmdb_enabled: !!(elTmdb && elTmdb.checked),
    bluray_scrape_enabled: !!(el && el.checked),
    bluraydiscde_scrape_enabled: !!(el2 && el2.checked),
  };
  try {
    const r = await fetch(`${API}/settings/sources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!r.ok) {
      showStatus('sourceSettingsStatus', d.error || t('js.saveFailed'), 'error');
      return;
    }
    showStatus('sourceSettingsStatus', t('js.sourceSettingsSaved'), 'success');
  } catch(e) {
    showStatus('sourceSettingsStatus', t('js.error', e.message), 'error');
  }
}

function queueActionLabel(item) {
  if (!item || !item.method || !item.url) return t('js.unknownAction');
  const u = item.url;
  const m = item.method;

  // Escape HTML to prevent XSS from stored titles
  const esc = s => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // Extract movie ID from URL and look up title in local cache
  const idMatch = u.match(/\/api\/(?:movies|watchlist|watched)\/(\d+)/);
  const movieId = idMatch ? parseInt(idMatch[1]) : null;
  const cachedMovie = movieId ? allMovies.find(x => x.id === movieId) : null;

  // Also try to extract title from stored body (e.g. for edits or adds)
  let bodyTitle = null;
  try { const p = item.body ? JSON.parse(item.body) : {}; bodyTitle = p.title || null; } catch(e) {}

  const title = esc(cachedMovie?.title || bodyTitle || null);
  const titleTag = title ? ` <span style="color:var(--accent)">"${title}"</span>` : '';

  // Watched date
  let watchDate = '';
  try { watchDate = item.body ? (JSON.parse(item.body).watched_at || '') : ''; } catch(e) {}

  // Movies
  if (m === 'POST' && u === '/api/movies') return t('js.actionAdd') + titleTag;
  if (m === 'PUT'  && /\/api\/movies\/\d+$/.test(u))        return t('js.actionUpdate') + titleTag;
  if (m === 'PUT'  && /\/api\/movies\/\d+\/groups$/.test(u)) return t('js.actionUpdateGroups') + titleTag;
  if (m === 'DELETE' && /\/api\/movies\/\d+$/.test(u))      return t('js.actionDelete') + titleTag;
  if (m === 'POST' && u === '/api/movies/bulk-delete')       return t('js.actionBulkDelete');

  // Watchlist
  if (m === 'POST'   && /\/api\/watchlist\/\d+$/.test(u)) return t('js.actionWatchlistAdd') + titleTag;
  if (m === 'DELETE' && /\/api\/watchlist\/\d+$/.test(u)) return t('js.actionWatchlistRemove') + titleTag;
  if (m === 'POST'   && u.startsWith('/api/watchlist/bulk')) return t('js.actionWatchlistBulk');

  // Watched / watch history
  if (m === 'POST' && /\/api\/watched\/\d+$/.test(u))
    return t('js.actionWatched') + titleTag + (watchDate ? ` (${watchDate})` : '');
  if (m === 'DELETE' && /\/api\/watched\/entry\/\d+$/.test(u)) return t('js.actionWatchedRemove');

  return `${m} ${u}`;
}

function loadQueueSettings() {
  const list = document.getElementById('queueList');
  if (!list) return;
  const queue = readWriteQueue();

  if (!queue.length) {
    list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('js.noQueuedActions')}</div>`;
    return;
  }

  list.innerHTML = queue.map(item => {
    const ts = new Date(item.created_at || Date.now()).toLocaleString();
    const label = queueActionLabel(item);
    return `
      <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px; flex-wrap:wrap;">
        <div style="font-size:1.05rem;">⏳</div>
        <div style="flex:1; min-width:200px;">
          <div style="font-weight:500; font-size:0.85rem;">${label}</div>
          <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">${ts}</div>
        </div>
        <button class="btn btn-danger" style="padding:6px 10px; font-size:0.75rem;" onclick="removeQueuedMutation('${item.id}')">✕</button>
      </div>
    `;
  }).join('');
}

function removeQueuedMutation(id) {
  const queue = readWriteQueue().filter(item => item.id !== id);
  writeWriteQueue(queue);
  updateQueueIndicator();
  loadQueueSettings();
}

function clearQueuedMutations() {
  if (!confirm(t('js.confirmClearQueue'))) return;
  writeWriteQueue([]);
  updateQueueIndicator();
  loadQueueSettings();
  showStatus('queueStatus', t('js.queueCleared'), 'success');
}

async function syncQueueNow() {
  if (!navigator.onLine) {
    showStatus('queueStatus', t('js.offlineSyncError'), 'error');
    return;
  }
  showStatus('queueStatus', t('js.synchronizing'), 'info');
  const before = readWriteQueue().length;
  await flushQueuedMutations();
  const after = readWriteQueue().length;
  if (after === 0) {
    showStatus('queueStatus', t('js.syncComplete', before), 'success');
  } else if (after < before) {
    showStatus('queueStatus', t('js.syncPartial', before - after, after), 'info');
  } else {
    showStatus('queueStatus', t('js.syncFailed'), 'error');
  }
  loadQueueSettings();
}

async function loadDbStats() {
  try {
    const r = await fetch(`${API}/settings/db-stats`);
    const d = await r.json();
    const el = document.getElementById('dbStatsContent');
    el.innerHTML = `
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:12px;">
        <div style="background:var(--surface2); padding:14px; border-radius:6px; text-align:center; border:1px solid var(--border);">
          <div style="font-family:'DM Serif Display',serif; font-size:1.6rem; color:var(--accent);">${d.movie_count}</div>
          <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:.05em;">${t('js.movies')}</div>
        </div>
        <div style="background:var(--surface2); padding:14px; border-radius:6px; text-align:center; border:1px solid var(--border);">
          <div style="font-family:'DM Serif Display',serif; font-size:1.6rem; color:var(--accent);">${d.poster_count}</div>
          <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:.05em;">${t('js.posters')}</div>
        </div>
        <div style="background:var(--surface2); padding:14px; border-radius:6px; text-align:center; border:1px solid var(--border);">
          <div style="font-family:'DM Serif Display',serif; font-size:1.6rem; color:var(--text-muted);">${(d.db_size / 1024).toFixed(0)} KB</div>
          <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:.05em;">${t('js.database')}</div>
        </div>
        <div style="background:var(--surface2); padding:14px; border-radius:6px; text-align:center; border:1px solid var(--border);">
          <div style="font-family:'DM Serif Display',serif; font-size:1.6rem; color:var(--text-muted);">${(d.poster_size / 1024 / 1024).toFixed(1)} MB</div>
          <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:.05em;">${t('js.postersSize')}</div>
        </div>
      </div>`;
  } catch(e) {}
}

async function createBackup() {
  showStatus('settingsBackupStatus', t('js.creatingBackup'), 'info');
  try {
    const r = await fetch(`${API}/settings/backup`, { method: 'POST', headers: authHeaders() });
    const d = await r.json();
    if (d.error) { showStatus('settingsBackupStatus', d.error, 'error'); return; }
    showStatus('settingsBackupStatus', t('js.backupCreated', d.name, (d.size/1024).toFixed(0)), 'success');
    loadBackups();
  } catch(e) {
    showStatus('settingsBackupStatus', t('js.error', e.message), 'error');
  }
}

async function loadBackups() {
  try {
    const r = await fetch(`${API}/settings/backups`, { headers: authHeaders() });
    const backups = await r.json();
    const el = document.getElementById('backupsList');
    if (!backups.length) {
      el.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('js.noBackups')}</div>`;
      return;
    }
    el.innerHTML = backups.map(b => `
      <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px; flex-wrap:wrap;">
        <div style="font-size:1.2rem;">📦</div>
        <div style="flex:1; min-width:180px;">
          <div style="font-weight:500; font-size:0.85rem;">${b.name}</div>
          <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">
            ${(b.size/1024).toFixed(0)} KB · ${b.poster_count} posters${b.movie_count ? ' · ' + b.movie_count + ' films' : ''}${b.format === 'v1' ? ' · legacy' : ''}
          </div>
        </div>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-success" style="padding:6px 10px; font-size:0.75rem;" onclick="restoreBackup('${b.name}')">${t('js.restore')}</button>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.75rem;" onclick="downloadBackup('${b.name}')">${t('js.download')}</button>
          <button class="btn btn-danger" style="padding:6px 10px; font-size:0.75rem;" onclick="deleteBackup('${b.name}')">✕</button>
        </div>
      </div>
    `).join('');
  } catch(e) {}
}

async function downloadBackup(name) {
  try {
    const r = await fetch(`${API}/settings/backup/${encodeURIComponent(name)}/download`, { headers: authHeaders() });
    if (!r.ok) { showStatus('settingsBackupStatus', t('js.downloadFailed'), 'error'); return; }
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name + '.tar.gz';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch(e) {
    showStatus('settingsBackupStatus', t('js.error', e.message), 'error');
  }
}

async function uploadBackup(input) {
  const file = input.files[0];
  input.value = '';
  if (!file) return;
  if (!file.name.match(/\.(tar\.gz|tgz)$/i)) {
    showStatus('settingsBackupStatus', t('js.tarGzOnly'), 'error');
    return;
  }
  showStatus('settingsBackupStatus', t('js.uploading'), 'info');
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`${API}/settings/backup/upload`, {
      method: 'POST',
      headers: authHeaders(),
      body: fd
    });
    const d = await r.json();
    if (!r.ok || d.error) { showStatus('settingsBackupStatus', d.error || t('js.uploadFailed'), 'error'); return; }
    showStatus('settingsBackupStatus', t('js.uploadDone'), 'success');
    loadBackups();
    // Auto-trigger restore for the uploaded backup
    restoreBackup(d.name);
  } catch(e) {
    showStatus('settingsBackupStatus', t('js.error', e.message), 'error');
  }
}

async function restoreBackup(name, groupMapping) {
  if (!groupMapping && !confirm(t('js.confirmRestore', name))) return;
  showStatus('settingsBackupStatus', t('js.restoring'), 'info');
  try {
    const body = groupMapping ? { group_mapping: groupMapping } : {};
    const r = await fetch(`${API}/settings/restore/${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body)
    });
    const d = await r.json();

    // Group conflict: show resolution dialog
    if (r.status === 409 && d.status === 'groups_conflict') {
      showGroupConflictDialog(name, d);
      return;
    }
    if (d.error) { showStatus('settingsBackupStatus', d.error, 'error'); return; }
    showStatus('settingsBackupStatus', t('js.restored', name), 'success');
    loadDbStats(); loadCollection(); loadStats(); loadBackups();
  } catch(e) {
    showStatus('settingsBackupStatus', t('js.error', e.message), 'error');
  }
}

function showGroupConflictDialog(backupName, conflictData) {
  const missing = conflictData.missing_groups;
  const existing = conflictData.existing_groups;

  let html = `<div style="position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;" id="groupConflictOverlay">
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:480px;width:90%;max-height:80vh;overflow-y:auto;">
      <div style="font-family:'DM Serif Display',serif;font-size:1.1rem;margin-bottom:16px;">${t('js.groupConflictTitle')}</div>
      <div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:16px;">${t('js.groupConflictDesc', conflictData.movie_count)}</div>`;

  missing.forEach((g, i) => {
    html += `<div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px;">
      <div style="font-weight:500;font-size:0.88rem;margin-bottom:8px;">📁 ${g}</div>
      <select id="groupAction_${i}" onchange="toggleGroupAssign(${i})" style="width:100%;padding:6px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-size:0.82rem;">
        <option value="create">${t('js.groupActionCreate')}</option>
        <option value="skip">${t('js.groupActionSkip')}</option>
        ${existing.map(eg => `<option value="assign_${eg.id}">${t('js.groupActionAssign', eg.name)}</option>`).join('')}
      </select>
    </div>`;
  });

  html += `<div style="display:flex;gap:10px;margin-top:16px;">
      <button class="btn btn-primary" onclick="applyGroupConflict('${backupName}')">${t('js.groupConflictApply')}</button>
      <button class="btn btn-secondary" onclick="document.getElementById('groupConflictOverlay').remove()">${t('js.cancel')}</button>
    </div>
    </div></div>`;

  // Store missing group names for the apply function
  window._groupConflictMissing = missing;
  document.body.insertAdjacentHTML('beforeend', html);
}

function applyGroupConflict(backupName) {
  const missing = window._groupConflictMissing || [];
  const mapping = {};
  missing.forEach((g, i) => {
    const sel = document.getElementById(`groupAction_${i}`);
    const val = sel ? sel.value : 'skip';
    if (val === 'create') {
      mapping[g] = { action: 'create' };
    } else if (val === 'skip') {
      mapping[g] = { action: 'skip' };
    } else if (val.startsWith('assign_')) {
      mapping[g] = { action: 'assign', group_id: parseInt(val.split('_')[1]) };
    }
  });
  document.getElementById('groupConflictOverlay').remove();
  restoreBackup(backupName, mapping);
}

async function deleteBackup(name) {
  if (!confirm(t('js.confirmDeleteBackup', name))) return;
  await fetch(`${API}/settings/backup/${name}`, { method: 'DELETE', headers: authHeaders() });
  loadBackups();
}

async function resetDatabase() {
  if (!confirm(t('js.confirmReset1'))) return;
  if (!confirm(t('js.confirmReset2'))) return;
  try {
    const r = await fetch(`${API}/settings/reset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: 'RESET' })
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    alert(t('js.resetComplete'));
    loadDbStats();
    loadCollection();
    loadStats();
  } catch(e) {
    alert(t('js.error', e.message));
  }
}

// ── Language & MCP Settings ───────────────────────────────────────────────────
function changeLanguage(lang) {
  setLanguage(lang);
  _updateWatchedBtn();
  _updateWatchlistBtn();
  loadCollection();
  loadStats();
  if (document.getElementById('panel-settings').classList.contains('active')) loadSettings();
  if (document.getElementById('panel-lists').classList.contains('active')) {
    const activeListsSub = document.querySelector('[data-lists-sub].active');
    if (activeListsSub) switchListsSubmenu(activeListsSub.getAttribute('data-lists-sub'));
  }
}

async function saveMcpSettings() {
  const enabled = document.getElementById('mcpEnabledToggle').checked;
  try {
    const r = await fetch(`${API}/settings/mcp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ mcp_enabled: enabled })
    });
    await r.json();
    showStatus('mcpSettingsStatus', t('js.mcpSettingsSaved'), 'success');
  } catch(e) {
    showStatus('mcpSettingsStatus', t('js.error', e.message), 'error');
  }
}

async function saveDebugSettings() {
  const el = document.getElementById('debugEnabledToggle');
  const enabled = !!(el && el.checked);
  try {
    const r = await fetch(`${API}/settings/debug`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ debug_enabled: enabled })
    });
    await r.json();
    debugModeEnabled = enabled;
    applyDebugVisibility();
    showStatus('mcpSettingsStatus', t('js.advancedSettingsSaved'), 'success');
  } catch(e) {
    showStatus('mcpSettingsStatus', t('js.error', e.message), 'error');
  }
}

async function loadDebugSettings() {
  try {
    const r = await fetch(`${API}/settings/debug`, { headers: authHeaders() });
    const d = await r.json();
    debugModeEnabled = d.debug_enabled === true;
    const el = document.getElementById('debugEnabledToggle');
    if (el) el.checked = debugModeEnabled;
    applyDebugVisibility();
  } catch(e) {
    debugModeEnabled = false;
    const el = document.getElementById('debugEnabledToggle');
    if (el) el.checked = false;
    applyDebugVisibility();
  }
}

async function loadMcpSettings() {
  try {
    const r = await fetch(`${API}/settings/mcp`, { headers: authHeaders() });
    const d = await r.json();
    document.getElementById('mcpEnabledToggle').checked = d.mcp_enabled !== false;
  } catch(e) {}
}

