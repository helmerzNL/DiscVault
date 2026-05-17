// ── Backup / Restore / Reset ──────────────────────────────────────────────────

async function loadSettings() {
  loadDbStats();
  loadAuthSettings();
  loadQueueSettings();
  loadSourceSettings();
  loadDebugSettings(); // also initialises showLocalTitleToggle
  loadLanguagePicker();
  loadCollectorsModeSetting();
  loadGroupEditionsSetting();
  loadDigitalSources();
}

// ── Rating country picker ────────────────────────────────────────────────────

const RATING_COUNTRY_NAMES = {
  nl: { US: 'Verenigde Staten', GB: 'Verenigd Koninkrijk', CA: 'Canada', NL: 'Nederland',    FR: 'Frankrijk',  DE: 'Duitsland',   ES: 'Spanje',   PT: 'Portugal',   IT: 'Italië' },
  en: { US: 'United States',    GB: 'United Kingdom',     CA: 'Canada', NL: 'Netherlands',   FR: 'France',     DE: 'Germany',     ES: 'Spain',    PT: 'Portugal',   IT: 'Italy'  },
  fr: { US: 'États-Unis',       GB: 'Royaume-Uni',        CA: 'Canada', NL: 'Pays-Bas',      FR: 'France',     DE: 'Allemagne',   ES: 'Espagne',  PT: 'Portugal',   IT: 'Italie' },
  de: { US: 'USA',              GB: 'Vereinigtes Königreich', CA: 'Kanada', NL: 'Niederlande', FR: 'Frankreich', DE: 'Deutschland', ES: 'Spanien',  PT: 'Portugal',   IT: 'Italien'},
  es: { US: 'Estados Unidos',   GB: 'Reino Unido',        CA: 'Canadá', NL: 'Países Bajos',  FR: 'Francia',    DE: 'Alemania',    ES: 'España',   PT: 'Portugal',   IT: 'Italia' },
  pt: { US: 'Estados Unidos',   GB: 'Reino Unido',        CA: 'Canadá', NL: 'Países Baixos', FR: 'França',     DE: 'Alemanha',    ES: 'Espanha',  PT: 'Portugal',   IT: 'Itália' },
  it: { US: 'Stati Uniti',      GB: 'Regno Unito',        CA: 'Canada', NL: 'Paesi Bassi',   FR: 'Francia',    DE: 'Germania',    ES: 'Spagna',   PT: 'Portogallo', IT: 'Italia' },
};
const RATING_COUNTRIES_ORDER = ['NL', 'DE', 'FR', 'ES', 'PT', 'IT', 'US', 'GB', 'CA'];

function loadRatingCountryPicker() {
  const container = document.getElementById('ratingCountryPicker');
  if (!container) return;
  const names = RATING_COUNTRY_NAMES[currentLang] || RATING_COUNTRY_NAMES.en;
  container.innerHTML = RATING_COUNTRIES_ORDER.map(c => {
    const active = preferredRatingCountry === c;
    return `<button type="button" onclick="selectRatingCountry('${c}')" id="rcBtn_${c}"
      style="display:flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid ${active ? 'var(--accent)' : 'var(--border)'};border-radius:8px;background:${active ? 'rgba(232,197,71,0.08)' : 'var(--surface2)'};cursor:pointer;font-size:0.82rem;color:var(--text);">
      <img src="/flags/${c.toLowerCase()}.svg" width="20" height="15" alt="${c}" style="border-radius:2px;flex-shrink:0;">
      <span>${names[c] || c}</span>
    </button>`;
  }).join('');
}

function selectRatingCountry(code) {
  preferredRatingCountry = code;
  localStorage.setItem('dv_rating_country', code);
  loadRatingCountryPicker();
  showStatus('preferencesStatus', t('js.advancedSettingsSaved'), 'success');
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

  // Hide groups-update items that are paired with a movie update for the same ID
  // (they're an implementation detail of saveEdit and clutter the display)
  const pairedMovieIds = new Set(
    queue
      .filter(item => item.method === 'PUT' && /\/api\/movies\/\d+$/.test(item.url))
      .map(item => item.url.match(/\/api\/movies\/(\d+)$/)[1])
  );
  const displayQueue = queue.filter(item => {
    const gm = item.url.match(/\/api\/movies\/(\d+)\/groups$/);
    return !(gm && item.method === 'PUT' && pairedMovieIds.has(gm[1]));
  });

  list.innerHTML = displayQueue.map(item => {
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

// ── Language picker ─────────────────────────────────────────────────────────
const LANGS_ORDER    = ['nl', 'en', 'fr', 'de', 'es', 'pt', 'it'];
const LANG_NATIVE    = { nl: 'Nederlands', en: 'English', fr: 'Français', de: 'Deutsch', es: 'Español', pt: 'Português', it: 'Italiano' };
const LANG_FLAG_CODE = { nl: 'nl', en: 'gb', fr: 'fr', de: 'de', es: 'es', pt: 'pt', it: 'it' };

function loadLanguagePicker() {
  const container = document.getElementById('languagePicker');
  if (!container) return;
  container.innerHTML = LANGS_ORDER.map(lang => {
    const active = currentLang === lang;
    return `<button type="button" onclick="changeLanguage('${lang}')" id="langBtn_${lang}"
      style="display:flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid ${active ? 'var(--accent)' : 'var(--border)'};border-radius:8px;background:${active ? 'rgba(232,197,71,0.08)' : 'var(--surface2)'};cursor:pointer;font-size:0.82rem;color:var(--text);">
      <img src="/flags/${LANG_FLAG_CODE[lang]}.svg" width="20" height="15" alt="${lang}" style="border-radius:2px;flex-shrink:0;">
      <span>${LANG_NATIVE[lang]}</span>
    </button>`;
  }).join('');
}

// ── Language & MCP Settings ───────────────────────────────────────────────────
function changeLanguage(lang) {
  setLanguage(lang);
  loadRatingCountryPicker();
  loadLanguagePicker();
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
  // Load display settings together with debug settings
  try {
    const r2 = await fetch(`${API}/settings/display`, { headers: authHeaders() });
    const d2 = await r2.json();
    showLocalTitle = d2.show_local_title !== false;
    const el2 = document.getElementById('showLocalTitleToggle');
    if (el2) el2.checked = showLocalTitle;
  } catch(e) {
    showLocalTitle = true;
    const el2 = document.getElementById('showLocalTitleToggle');
    if (el2) el2.checked = true;
  }
  loadRatingCountryPicker();
}

async function saveDisplaySettings() {
  const el = document.getElementById('showLocalTitleToggle');
  const val = !!(el && el.checked);
  try {
    const r = await fetch(`${API}/settings/display`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ show_local_title: val })
    });
    await r.json();
    showLocalTitle = val;
    showStatus('preferencesStatus', t('js.advancedSettingsSaved'), 'success');
  } catch(e) {
    showStatus('preferencesStatus', t('js.error', e.message), 'error');
  }
}

async function loadMcpSettings() {
  try {
    const r = await fetch(`${API}/settings/mcp`, { headers: authHeaders() });
    const d = await r.json();
    const el = document.getElementById('mcpEnabledToggle');
    if (el) el.checked = d.mcp_enabled !== false;
  } catch(e) {
    // Default: MCP is enabled — restore correct default if fetch fails
    const el = document.getElementById('mcpEnabledToggle');
    if (el) el.checked = true;
  }
}

// ── Edition grouping preference ───────────────────────────────────────────────

function loadCollectorsModeSetting() {
  const toggle = document.getElementById('collectorsModeToggle');
  if (toggle) toggle.checked = collectorsMode;
  if (collectorsMode) {
    document.body.classList.add('collectors-mode');
  } else {
    document.body.classList.remove('collectors-mode');
  }
}

function saveCollectorsModeSetting() {
  const toggle = document.getElementById('collectorsModeToggle');
  collectorsMode = !!(toggle && toggle.checked);
  localStorage.setItem('dv_collectors_mode', collectorsMode ? 'true' : 'false');
  if (collectorsMode) {
    document.body.classList.add('collectors-mode');
  } else {
    document.body.classList.remove('collectors-mode');
    // When disabling, also disable grouping
    groupEditionsEnabled = false;
    localStorage.setItem('dv_group_editions', 'false');
    const gt = document.getElementById('groupEditionsToggle');
    if (gt) gt.checked = false;
  }
  loadCollection();
  showStatus('preferencesStatus', t('js.advancedSettingsSaved'), 'success');
}

function loadGroupEditionsSetting() {
  const toggle = document.getElementById('groupEditionsToggle');
  if (toggle) toggle.checked = groupEditionsEnabled;
  const badgeToggle = document.getElementById('showDigitalBadgesToggle');
  if (badgeToggle) badgeToggle.checked = showDigitalBadges;
}

function saveGroupEditionsSetting() {
  const toggle = document.getElementById('groupEditionsToggle');
  groupEditionsEnabled = !!(toggle && toggle.checked);
  localStorage.setItem('dv_group_editions', groupEditionsEnabled ? 'true' : 'false');
  loadCollection();
  showStatus('preferencesStatus', t('js.advancedSettingsSaved'), 'success');
}

function saveDigitalBadgesSetting() {
  const toggle = document.getElementById('showDigitalBadgesToggle');
  showDigitalBadges = !!(toggle && toggle.checked);
  localStorage.setItem('dv_digital_badges', showDigitalBadges ? 'true' : 'false');
  showStatus('preferencesStatus', t('js.advancedSettingsSaved'), 'success');
}

// ── Digital library management ────────────────────────────────────────────────

async function loadDigitalSources() {
  const container = document.getElementById('digitalSourcesList');
  if (!container) return;
  try {
    const r = await fetch(`${API}/digital-sources`, { headers: authHeaders() });
    if (!r.ok) { container.innerHTML = ''; return; }
    const sources = await r.json();
    if (!sources.length) {
      container.innerHTML = `<div style="font-size:0.82rem; color:var(--text-muted);" data-i18n="digital.noSources">${t('digital.noSources')}</div>`;
      return;
    }
    container.innerHTML = sources.map(s => {
      const typeLabel = s.type === 'plex' ? '🟡 Plex' : '🔵 Jellyfin';
      const syncedInfo = s.last_synced
        ? `${s.item_count || 0} items · ${t('digital.lastSynced')} ${s.last_synced.slice(0, 10)}`
        : t('digital.neverSynced');
      return `<div style="padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:8px; margin-bottom:8px;">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
          <div>
            <span style="font-weight:500; font-size:0.88rem;">${s.name}</span>
            <span style="font-size:0.76rem; color:var(--text-muted); margin-left:6px;">${typeLabel}</span>
            <div style="font-size:0.74rem; color:var(--text-muted); margin-top:2px;">${s.base_url}</div>
            <div style="font-size:0.74rem; color:var(--text-muted);" id="syncStatus_${s.id}">${syncedInfo}</div>
          </div>
          <div style="display:flex; gap:6px; flex-shrink:0;">
            <button class="btn btn-secondary" onclick="syncDigitalSource(${s.id})" style="padding:5px 10px; font-size:0.76rem;" data-i18n="digital.syncBtn">${t('digital.syncBtn')}</button>
            <button class="btn btn-danger" onclick="removeDigitalSource(${s.id})" style="padding:5px 10px; font-size:0.76rem;" data-i18n="digital.removeBtn">${t('digital.removeBtn')}</button>
          </div>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    if (container) container.innerHTML = `<div style="color:var(--danger); font-size:0.82rem;">${t('js.error', e.message)}</div>`;
  }
}

function openAddDigitalSource(type) {
  const modal = document.getElementById('addDigitalSourceModal');
  if (!modal) { console.error('[DiscVault] addDigitalSourceModal not found in DOM'); return; }
  const typeEl = document.getElementById('addDigitalSourceType');
  if (typeEl) typeEl.value = type;
  const titleEl = document.getElementById('addDigitalSourceTitle');
  if (titleEl) titleEl.textContent = type === 'plex'
    ? '🟡 ' + t('digital.addPlexTitle')
    : '🔵 ' + t('digital.addJellyfinTitle');
  const labelEl = document.getElementById('addDigitalTokenLabel');
  if (labelEl) labelEl.textContent = type === 'plex'
    ? t('digital.plexToken')
    : t('digital.jellyfinToken');
  const nameEl  = document.getElementById('addDigitalName');   if (nameEl)  nameEl.value  = '';
  const urlEl   = document.getElementById('addDigitalUrl');    if (urlEl)   urlEl.value   = '';
  const tokenEl = document.getElementById('addDigitalToken');  if (tokenEl) tokenEl.value = '';
  const statusEl = document.getElementById('addDigitalSourceStatus');
  if (statusEl) statusEl.className = 'status-msg';
  modal.style.display = 'flex';
}

function closeAddDigitalSource() {
  document.getElementById('addDigitalSourceModal').style.display = 'none';
}

async function saveAddDigitalSource() {
  const type   = document.getElementById('addDigitalSourceType').value;
  const name   = document.getElementById('addDigitalName').value.trim();
  const url    = document.getElementById('addDigitalUrl').value.trim();
  const token  = document.getElementById('addDigitalToken').value.trim();
  if (!name || !url) {
    showStatus('addDigitalSourceStatus', t('digital.nameUrlRequired'), 'error');
    return;
  }
  try {
    const r = await fetch(`${API}/digital-sources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ name, type, base_url: url, token })
    });
    const d = await r.json();
    if (!r.ok) { showStatus('addDigitalSourceStatus', d.error || t('js.saveFailed'), 'error'); return; }
    closeAddDigitalSource();
    loadDigitalSources();
  } catch(e) {
    showStatus('addDigitalSourceStatus', t('js.error', e.message), 'error');
  }
}

async function testDigitalSourceFromModal() {
  const type  = document.getElementById('addDigitalSourceType').value;
  const name  = document.getElementById('addDigitalName').value.trim();
  const url   = document.getElementById('addDigitalUrl').value.trim();
  const token = document.getElementById('addDigitalToken').value.trim();
  if (!url) { showStatus('addDigitalSourceStatus', t('digital.urlRequired'), 'error'); return; }
  showStatus('addDigitalSourceStatus', t('digital.testing'), 'info');
  try {
    // Save temporarily to test (will be overridden if user saves)
    const r = await fetch(`${API}/digital-sources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ name: name || 'test', type, base_url: url, token })
    });
    const d = await r.json();
    if (!r.ok) { showStatus('addDigitalSourceStatus', d.error || t('js.error', ''), 'error'); return; }
    const testId = d.id;
    const r2 = await fetch(`${API}/digital-sources/${testId}/test`, { method: 'POST', headers: authHeaders() });
    const d2 = await r2.json();
    // Remove temporary source
    await fetch(`${API}/digital-sources/${testId}`, { method: 'DELETE', headers: authHeaders() });
    showStatus('addDigitalSourceStatus', d2.ok ? `✓ ${d2.message}` : `✗ ${d2.message}`, d2.ok ? 'success' : 'error');
  } catch(e) {
    showStatus('addDigitalSourceStatus', t('js.error', e.message), 'error');
  }
}

async function removeDigitalSource(id) {
  if (!confirm(t('digital.confirmRemove'))) return;
  try {
    await fetch(`${API}/digital-sources/${id}`, { method: 'DELETE', headers: authHeaders() });
    loadDigitalSources();
  } catch(e) {}
}

async function syncDigitalSource(id) {
  const statusEl = document.getElementById(`syncStatus_${id}`);
  if (statusEl) statusEl.textContent = t('digital.syncing');
  try {
    await fetch(`${API}/digital-sources/${id}/sync`, { method: 'POST', headers: authHeaders() });
    // Poll for completion
    const poll = setInterval(async () => {
      const r = await fetch(`${API}/digital-sources/${id}/sync-status`, { headers: authHeaders() });
      const d = await r.json();
      if (statusEl) {
        if (d.status === 'running') {
          statusEl.textContent = t('digital.syncProgress', d.progress || 0, d.total || '?');
        } else if (d.status === 'done') {
          clearInterval(poll);
          loadDigitalSources();
          showStatus('digitalSourceStatus', t('digital.syncDone'), 'success');
        } else if (d.status === 'error') {
          clearInterval(poll);
          statusEl.textContent = t('js.error', d.error || '');
        }
      } else {
        clearInterval(poll);
      }
    }, 2000);
  } catch(e) {
    if (statusEl) statusEl.textContent = t('js.error', e.message);
  }
}

