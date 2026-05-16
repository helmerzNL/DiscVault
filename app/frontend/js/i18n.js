// ── i18n ──────────────────────────────────────────────────────────────────────
let LANGS = {};

async function loadTranslations() {
  try {
    const r = await fetch('/i18n/translations.json');
    if (r.ok) LANGS = await r.json();
  } catch(e) {
    console.warn('[DiscVault] Could not load translations');
  }
}


let currentLang = localStorage.getItem('dv_lang') || 'nl';
let _detailReturnTab = 'collection';
let _personReturnPanel = 'movie-detail';

function t(key, ...args) {
  let s = (LANGS[currentLang] && LANGS[currentLang][key]) || (LANGS.nl[key]) || key;
  args.forEach((a, i) => { s = s.replaceAll('{' + i + '}', a); });
  return s;
}

function applyLanguage() {
  document.documentElement.lang = currentLang;
  document.title = t('js.pageTitle');
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => {
    el.setAttribute('aria-label', t(el.dataset.i18nAria));
  });
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
}

function setLanguage(lang) {
  currentLang = lang;
  localStorage.setItem('dv_lang', lang);
  applyLanguage();
}

const API = '/api';
let currentBarcode = '';
let currentMovieData = {};
let currentMovieId = null;
let currentPersonId = null;
let debugModeEnabled = false;
let showLocalTitle = true;
let allMovies = [];
let activeFormat = '';
let _detailNavList = [];   // ordered list of movie IDs for swipe navigation
let scannerRunning = false;
const WRITE_QUEUE_KEY = 'dv_write_queue';
let appVersionLabel = 'dev';
let isBeta = false;

async function loadAppVersion() {
  try {
    const r = await fetch(`/version.json?t=${Date.now()}`, { cache: 'no-store' });
    if (r.ok) {
      const d = await r.json();
      if (d && d.version) appVersionLabel = String(d.version);
    }
  } catch(e) {}

  isBeta = appVersionLabel.startsWith('beta');
  if (isBeta) applyBetaBranding();

  const loginEl = document.getElementById('loginVersion');
  const settingsEl = document.getElementById('settingsVersion');
  if (loginEl) loginEl.textContent = appVersionLabel;
  if (settingsEl) settingsEl.textContent = appVersionLabel;
}

function applyBetaBranding() {
  // Swap favicon and apple-touch-icon to beta variants
  const iconSwaps = {
    '/favicon-32.png': '/favicon-32-beta.png',
    '/favicon-192.png': '/favicon-192-beta.png',
    '/apple-touch-icon.png': '/apple-touch-icon-beta.png',
  };
  document.querySelectorAll('link[rel="icon"], link[rel="apple-touch-icon"]').forEach(link => {
    const href = link.getAttribute('href');
    if (href && iconSwaps[href]) link.setAttribute('href', iconSwaps[href]);
  });
  // Swap manifest to beta version
  const manifestLink = document.querySelector('link[rel="manifest"]');
  if (manifestLink) manifestLink.setAttribute('href', '/manifest-beta.json');
  // Update page title
  document.title = 'DiscVault Beta — Collectie Beheer';
}

function setCachedData(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch(e) {}
}

function getCachedData(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw);
  } catch(e) {
    return fallback;
  }
}

function updateConnectionState() {
  document.body.classList.toggle('is-offline', !navigator.onLine);
}

function readWriteQueue() {
  return getCachedData(WRITE_QUEUE_KEY, []);
}

function writeWriteQueue(queue) {
  setCachedData(WRITE_QUEUE_KEY, queue);
}

function updateQueueIndicator() {
  const q = readWriteQueue();
  const count = q.length;
  document.body.classList.toggle('has-queue', count > 0);
  const pill = document.getElementById('queuePill');
  if (pill) pill.textContent = t('header.queue', count);
}

function isQueueableMutation(pathname, method) {
  if (!['POST', 'PUT', 'DELETE'].includes(method)) return false;
  if (pathname.startsWith('/api/movies')) {
    if (pathname.includes('/bulk-refresh')) return false;
    if (pathname.includes('/sync-all')) return false;
    if (pathname.includes('/sync-source')) return false;
    if (pathname.endsWith('/refresh')) return false;
    return true;
  }
  if (pathname.startsWith('/api/watchlist')) return true;
  if (pathname.startsWith('/api/watched')) return true;
  return false;
}

function serializeBodyForQueue(body) {
  if (body == null) return null;
  if (typeof body === 'string') return body;
  if (body instanceof URLSearchParams) return body.toString();
  return null;
}

function buildQueuedResponse(pathname, method, bodyText) {
  let parsed = {};
  try { parsed = bodyText ? JSON.parse(bodyText) : {}; } catch(e) {}

  const payload = {
    queued: true,
    offline: true,
    message: t('js.queuedMessage')
  };

  if (pathname === '/api/movies' && method === 'POST') {
    payload.movie = { title: parsed.title || t('js.unknownMovie') };
  }

  if (pathname.endsWith('/bulk-delete') && method === 'POST') {
    payload.deleted = Array.isArray(parsed.ids) ? parsed.ids.length : 0;
  }

  return new Response(JSON.stringify(payload), {
    status: 202,
    headers: { 'Content-Type': 'application/json' }
  });
}

function queueMutation(url, method, headers, bodyText) {
  const queue = readWriteQueue();
  queue.push({
    id: crypto.randomUUID(),
    url,
    method,
    headers: headers || {},
    body: bodyText,
    created_at: Date.now()
  });
  writeWriteQueue(queue);
  updateQueueIndicator();
}

async function flushQueuedMutations() {
  if (!navigator.onLine) return;
  const queue = readWriteQueue();
  if (!queue.length) return;

  const remaining = [];
  for (const item of queue) {
    try {
      const r = await _origFetch(item.url, {
        method: item.method,
        headers: { ...(item.headers || {}), ...authHeaders() },
        body: item.body || undefined
      });
      if (!r.ok) {
        remaining.push(item);
      }
    } catch(e) {
      remaining.push(item);
    }
  }

  writeWriteQueue(remaining);
  updateQueueIndicator();

  if (queue.length !== remaining.length) {
    await loadCollection();
    await loadStats();
  }

  loadQueueSettings();
}

function isMobileNav() {
  return window.matchMedia('(max-width: 900px)').matches;
}

function closeMobileMenu() {
  document.body.classList.remove('nav-open');
  const btn = document.getElementById('menuToggle');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

function toggleMobileMenu() {
  if (!isMobileNav()) return;
  const open = document.body.classList.toggle('nav-open');
  const btn = document.getElementById('menuToggle');
  if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function updateScrollState() {
  document.body.classList.toggle('is-scrolled', window.scrollY > 6);
}

function lockBodyScroll() {
  if (document.body.classList.contains('modal-open')) return;
  bodyScrollLockY = window.scrollY || window.pageYOffset || 0;
  document.body.style.top = `-${bodyScrollLockY}px`;
  document.body.classList.add('modal-open');
}

function unlockBodyScroll() {
  if (!document.body.classList.contains('modal-open')) return;
  document.body.classList.remove('modal-open');
  document.body.style.top = '';
  window.scrollTo(0, bodyScrollLockY);
}

function syncOverlayScrollLock() {
  // No-op: detail pages are panels now, no scroll lock needed
}

function applyDebugVisibility() {
  const movieRow = document.getElementById('modalMovieDebugRow');
  const personRow = document.getElementById('personDebugRow');
  const i18nBlock = document.getElementById('modalDebugI18nBlock');
  const personI18nBlock = document.getElementById('personDebugI18nBlock');
  if (movieRow) movieRow.style.display = debugModeEnabled ? 'flex' : 'none';
  if (personRow) personRow.style.display = debugModeEnabled ? 'flex' : 'none';
  if (i18nBlock) i18nBlock.style.display = debugModeEnabled ? 'block' : 'none';
  if (personI18nBlock) personI18nBlock.style.display = debugModeEnabled ? 'block' : 'none';
  filterMovies();
  filterSearchMovies();
}

function validateCriticalUiElements() {
  const requiredIds = [
    'modalTitle',
    'modalDirector',
    'modalMovieIdLabel',
    'modalMovieIdStatus',
    'personName',
    'personIdLabel',
    'personIdStatus',
    'panel-movie-detail',
    'panel-person-detail'
  ];
  const missing = requiredIds.filter(id => !document.getElementById(id));
  if (missing.length) {
    console.warn('[DiscVault] Missing critical UI elements:', missing.join(', '));
  }
}

