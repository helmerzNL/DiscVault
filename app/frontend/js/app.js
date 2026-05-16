// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  reorganizeToevoegenPanels();
  validateCriticalUiElements();
  applyDebugVisibility();
  document.getElementById('languageSelect').value = currentLang;
  applyLanguage();
  await loadAppVersion();
  await loadDebugSettings();
  const ok = await checkAuth();
  if (!ok) return;
  updateQueueIndicator();
  flushQueuedMutations();
  await loadStats();
  await loadCollection();
  loadInviteNotifications();
  _handleRoute();
}

function _renderHeaderStats() {
  const el = document.getElementById('headerStats');
  if (!el) return;
  const own = allMovies.filter(m => m.owner_id === currentUserId);
  const total = own.length;
  const counts = {};
  for (const m of own) { if (m.format) counts[m.format] = (counts[m.format] || 0) + 1; }
  const formats = Object.entries(counts).map(([f,c]) => `<span>${c}</span> ${f}`).join(' &nbsp;·&nbsp; ');
  el.innerHTML = t('js.statsTotal', total, formats || '');
}

async function loadStats(retries = 2) {
  try {
    const r = await fetch(`${API}/stats`);
    if (!r.ok) {
      if (retries > 0 && r.status >= 500) {
        await new Promise(ok => setTimeout(ok, 1500));
        return loadStats(retries - 1);
      }
      throw new Error(`HTTP ${r.status}`);
    }
    const d = await r.json();
    setCachedData('dv_stats_cache', d);
    _renderHeaderStats();
  } catch(e) {
    _renderHeaderStats();
  }
}

async function _loadGroupFilter() {
  const sel = document.getElementById('groupFilter');
  if (!sel) return;
  try {
    const groups = await fetch(`${API}/groups`).then(r => r.json());
    const prev = sel.value;
    sel.innerHTML = `<option value="">${t('collection.allCollections')}</option>`;
    if (authEnabled && currentUserId) {
      sel.insertAdjacentHTML('beforeend', `<option value="_mine">${t('collection.myMovies')}</option>`);
    }
    for (const g of groups) {
      sel.insertAdjacentHTML('beforeend', `<option value="${g.id}">${g.name}</option>`);
    }
    // Restore previous selection if still valid
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
    sel.style.display = groups.length || (authEnabled && currentUserId) ? '' : 'none';
  } catch(e) {
    sel.style.display = 'none';
  }
}

async function loadCollection(retries = 2) {
  // Load group filter options
  _loadGroupFilter();
  try {
    const r = await fetch(`${API}/movies`);
    if (!r.ok) {
      // Retry on server errors (502/503/504 = backend starting up)
      if (retries > 0 && r.status >= 500) {
        await new Promise(ok => setTimeout(ok, 1500));
        return loadCollection(retries - 1);
      }
      throw new Error(`HTTP ${r.status}`);
    }
    allMovies = await r.json();
    setCachedData('dv_movies_cache', allMovies);
    filterMovies();
    filterSearchMovies();
  } catch(e) {
    allMovies = getCachedData('dv_movies_cache', []);
    if (allMovies.length) {
      filterMovies();
      filterSearchMovies();
      const msg = navigator.onLine
        ? t('js.backendError')
        : t('js.offlineCache');
      document.getElementById('moviesGrid').insertAdjacentHTML(
        'afterbegin',
        `<div style="grid-column:1/-1; margin-bottom:12px; color:#ffd89a; border:1px solid rgba(240,144,64,.4); background:rgba(240,144,64,.08); border-radius:8px; padding:10px 12px; font-size:0.82rem; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">${msg} <button class="btn btn-secondary" onclick="loadCollection()" style="padding:4px 12px; font-size:0.78rem;">↻ ${t('collection.refresh')}</button></div>`
      );
    } else {
      document.getElementById('moviesGrid').innerHTML = `<div style="color: var(--danger); padding: 20px;">${t('js.offlineNoCache')}</div>`;
    }
  }
}

// ── Tab switching ─────────────────────────────────────────────────────────────
let currentToevoegenSub = 'scan';

function reorganizeToevoegenPanels() {
  // Move existing panel-scan content into addSubScan
  const scanDest = document.getElementById('addSubScan');
  const scanSrc  = document.getElementById('panel-scan');
  if (scanDest && scanSrc && scanDest.children.length === 0) {
    while (scanSrc.firstChild) scanDest.appendChild(scanSrc.firstChild);
    scanSrc.remove();
  }
  // Move existing panel-add content into addSubManual
  const manualDest = document.getElementById('addSubManual');
  const manualSrc  = document.getElementById('panel-add');
  if (manualDest && manualSrc && manualDest.children.length === 0) {
    while (manualSrc.firstChild) manualDest.appendChild(manualSrc.firstChild);
    manualSrc.remove();
  }
  // Move existing panel-import content into addSubImport
  const importDest = document.getElementById('addSubImport');
  const importSrc  = document.getElementById('panel-import');
  if (importDest && importSrc && importDest.children.length === 0) {
    while (importSrc.firstChild) importDest.appendChild(importSrc.firstChild);
    importSrc.remove();
  }
}

function switchToevoegen(sub) {
  currentToevoegenSub = sub;
  document.querySelectorAll('[data-toevoegen-sub]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-toevoegen-sub') === sub);
  });
  const map = { scan: 'addSubScan', manual: 'addSubManual', import: 'addSubImport' };
  document.querySelectorAll('#panel-toevoegen .settings-sub-section').forEach(s => s.classList.remove('active'));
  const el = document.getElementById(map[sub]);
  if (el) el.classList.add('active');
}

function switchTab(name) {
  const activeTabName = (name === 'logs') ? 'settings'
    : (name === 'scan' || name === 'import') ? 'toevoegen'
    : name;
  const panelName = (name === 'scan' || name === 'import') ? 'toevoegen' : name;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const selected = document.querySelector(`.tab[data-tab="${activeTabName}"]`);
  if (selected) selected.classList.add('active');
  const panel = document.getElementById(`panel-${panelName}`);
  if (panel) panel.classList.add('active');
  if (isMobileNav()) closeMobileMenu();
  if (panelName === 'collection') loadCollection();
  if (panelName === 'lists') loadWatchlist();
  if (name === 'logs') loadLogs();
  if (name === 'settings') loadSettings();
  if (name === 'import') switchToevoegen('import');
  if (name === 'search') {
    filterSearchMovies();
    const input = document.getElementById('searchPageInput');
    if (input) {
      setTimeout(() => input.focus(), 0);
    }
  }
  _pushRoute(_tabPath(name));
}

(async () => {
  await loadTranslations();
  init();
})();

// ── Push notifications ────────────────────────────────────────────────────────
let _pushSubscription = null;

function _urlB64ToUint8Array(b64) {
  const pad = '='.repeat((4 - b64.length % 4) % 4);
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

async function initPushNotifications() {
  const statusEl  = document.getElementById('pushNotifStatus');
  const subBtn    = document.getElementById('pushSubscribeBtn');
  const unsubBtn  = document.getElementById('pushUnsubscribeBtn');
  const testBtn   = document.getElementById('pushTestBtn');
  const prefsCard = document.getElementById('notifPrefsCard');
  if (!statusEl) return;

  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    statusEl.innerHTML = `<span style="color:var(--text-muted);">${t('settings.pushUnsupported')}</span>`;
    if (subBtn) subBtn.disabled = true;
    return;
  }
  const perm = Notification.permission;
  const reg  = await navigator.serviceWorker.ready;
  _pushSubscription = await reg.pushManager.getSubscription();

  if (_pushSubscription) {
    statusEl.innerHTML = `<span style="color:var(--success);">✓ ${t('settings.pushActive')}</span>`;
    if (subBtn)    subBtn.style.display    = 'none';
    if (unsubBtn)  unsubBtn.style.display  = '';
    if (testBtn)   testBtn.style.display   = '';
    if (prefsCard) { prefsCard.style.display = ''; loadNotifPrefs(); }
  } else if (perm === 'denied') {
    statusEl.innerHTML = `<span style="color:var(--danger);">${t('settings.pushBlocked')}</span>`;
    if (subBtn)    subBtn.disabled = true;
    if (prefsCard) prefsCard.style.display = 'none';
  } else {
    statusEl.innerHTML = `<span style="color:var(--text-muted);">${t('settings.pushInactive')}</span>`;
    if (subBtn)    { subBtn.style.display = ''; subBtn.disabled = false; }
    if (unsubBtn)  unsubBtn.style.display = 'none';
    if (testBtn)   testBtn.style.display  = 'none';
    if (prefsCard) prefsCard.style.display = 'none';
  }
}

async function subscribeToPush() {
  const statusEl = document.getElementById('pushNotifStatus');
  try {
    const r = await fetch(`${API}/push/vapid-public-key`);
    const { publicKey } = await r.json();
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _urlB64ToUint8Array(publicKey),
    });
    await fetch(`${API}/push/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub.toJSON()),
    });
    _pushSubscription = sub;
    await initPushNotifications();
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">${t('js.error', e.message)}</span>`;
  }
}

async function unsubscribeFromPush() {
  if (!_pushSubscription) return;
  const statusEl = document.getElementById('pushNotifStatus');
  try {
    const endpoint = _pushSubscription.endpoint;
    await _pushSubscription.unsubscribe();
    await fetch(`${API}/push/subscribe`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint }),
    });
    _pushSubscription = null;
    await initPushNotifications();
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">${t('js.error', e.message)}</span>`;
  }
}

async function testPushNotification() {
  const btn      = document.getElementById('pushTestBtn');
  const statusEl = document.getElementById('pushNotifStatus');
  if (btn) btn.disabled = true;
  try {
    const r    = await fetch(`${API}/push/test`, { method: 'POST' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) {
      const errors = data.errors || [data.error || r.statusText];
      const isStale = data.stale || errors.some(e => String(e).includes('410'));
      if (isStale) {
        // Subscription was revoked by the push service — clean up browser-side too.
        if (_pushSubscription) {
          try { await _pushSubscription.unsubscribe(); } catch(e) {}
          _pushSubscription = null;
        }
        await initPushNotifications();
        if (statusEl) statusEl.innerHTML = `<span style="color:var(--accent);">⚠ Je push-abonnement is verlopen. Schakel meldingen opnieuw in.</span>`;
      } else {
        const msg = errors.join('; ');
        if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">✗ ${msg}</span>`;
      }
    } else {
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--success);">✓ Verzonden — controleer je meldingen</span>`;
    }
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">✗ ${e.message}</span>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadNotifPrefs() {
  try {
    const r = await fetch(`${API}/push/prefs`);
    if (!r.ok) return;
    const prefs = await r.json();
    const el = document.getElementById('prefGroupInvite');
    if (el && prefs.group_invite !== undefined) el.checked = prefs.group_invite;
  } catch(e) { /* silently ignore */ }
}

async function saveNotifPref(key, value) {
  try {
    await fetch(`${API}/push/prefs`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    });
  } catch(e) { /* silently ignore */ }
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  });
  // Handle messages from the service worker (e.g. notification clicks on an open app).
  navigator.serviceWorker.addEventListener('message', event => {
    if (event.data && event.data.type === 'open-invites') {
      openInvitePanel();
    }
  });
}

// ── PWA install prompt ────────────────────────────────────────────────────────
let _deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  _deferredInstallPrompt = e;
  showPwaInstallBanner();
});

const _isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);

function showPwaInstallBanner() {
  // Don't show if already installed as PWA or dismissed recently
  if (window.matchMedia('(display-mode: standalone)').matches) return;
  if (navigator.standalone) return; // iOS
  if (localStorage.getItem('dv_pwa_dismissed')) return;
  const banner = document.getElementById('pwaInstallBanner');
  if (!banner) return;
  // On iOS: show instructions instead of install button
  const installBtn = document.getElementById('pwaInstallBtn');
  const iosHint = document.getElementById('pwaIosHint');
  if (_isIos && !_deferredInstallPrompt) {
    if (installBtn) installBtn.style.display = 'none';
    if (iosHint) iosHint.style.display = '';
  } else {
    if (installBtn) installBtn.style.display = '';
    if (iosHint) iosHint.style.display = 'none';
  }
  setTimeout(() => banner.classList.add('visible'), 600);
}

function pwaInstall() {
  if (_deferredInstallPrompt) {
    _deferredInstallPrompt.prompt();
    _deferredInstallPrompt.userChoice.then(r => {
      if (r.outcome === 'accepted') hidePwaInstallBanner();
      _deferredInstallPrompt = null;
    });
  }
}

function dismissPwaInstall() {
  localStorage.setItem('dv_pwa_dismissed', Date.now().toString());
  hidePwaInstallBanner();
}

function hidePwaInstallBanner() {
  const banner = document.getElementById('pwaInstallBanner');
  if (banner) banner.classList.remove('visible');
}

// For iOS/Safari (no beforeinstallprompt) – show after a delay
window.addEventListener('load', () => {
  if (!_deferredInstallPrompt && !window.matchMedia('(display-mode: standalone)').matches && !navigator.standalone) {
    const isSafari = /safari/i.test(navigator.userAgent) && !/chrome|crios|fxios/i.test(navigator.userAgent);
    if (_isIos || isSafari) {
      setTimeout(() => showPwaInstallBanner(), 2000);
    }
  }
});
