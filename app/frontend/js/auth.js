// ── Auth & Settings ───────────────────────────────────────────────────────────
let authToken = localStorage.getItem('dv_token') || '';
let authEnabled = false;
let currentUserId = null;
let currentUserRole = null;

function authHeaders() {
  return authToken ? { 'Authorization': 'Bearer ' + authToken } : {};
}

function updateLogoutButton() {
  const buttonIds = ['btnLogoutSettings', 'btnLogoutProfile'];
  const visible = !!authToken || authEnabled;
  for (const id of buttonIds) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    btn.style.display = visible ? 'inline-flex' : 'none';
  }
}

async function logoutApp() {
  authToken = '';
  currentUserId = null;
  currentUserRole = null;
  localStorage.removeItem('dv_token');
  updateLogoutButton();

  try {
    const sr = await _origFetch(`${API}/auth/status`);
    const status = await sr.json();
    authEnabled = !!status.auth_enabled;
    updateLogoutButton();
    if (status.auth_enabled) {
      showLoginOverlay();
      showStatus('loginStatus', t('js.loggedOut'), 'info');
    } else {
      hideLoginOverlay();
    }
  } catch(e) {
    showLoginOverlay();
  }
}

// Patch global fetch to inject auth header
const _origFetch = window.fetch;
window.fetch = async function(url, opts = {}) {
  const reqUrl = typeof url === 'string' ? url : (url?.url || '');
  const fullUrl = reqUrl ? new URL(reqUrl, window.location.origin) : null;
  const isApiRoute = !!(fullUrl && fullUrl.pathname.startsWith('/api/'));
  const isPublicAuth = !!(fullUrl && (
    fullUrl.pathname.startsWith('/api/auth/login') ||
    fullUrl.pathname.startsWith('/api/auth/register') ||
    fullUrl.pathname === '/api/auth/recovery'
  ));
  const method = (opts.method || 'GET').toUpperCase();

  if (isApiRoute && !isPublicAuth) {
    opts.headers = { ...(opts.headers || {}), ...authHeaders() };
  }

  const canQueue = !!(fullUrl && isQueueableMutation(fullUrl.pathname, method));
  const bodyText = serializeBodyForQueue(opts.body);

  if (canQueue && !navigator.onLine) {
    if (bodyText === null && opts.body != null) {
      return new Response(JSON.stringify({
        error: 'Offline queue ondersteunt dit requesttype niet.'
      }), { status: 503, headers: { 'Content-Type': 'application/json' } });
    }
    queueMutation(fullUrl.pathname + fullUrl.search, method, opts.headers, bodyText);
    return buildQueuedResponse(fullUrl.pathname, method, bodyText);
  }

  try {
    const r = await _origFetch(url, opts);

    if (r.status === 401 && !isPublicAuth) {
      const d = await r.clone().json().catch(() => ({}));
      if (d.auth_required) {
        authToken = '';
        localStorage.removeItem('dv_token');
        updateLogoutButton();
        showLoginOverlay();
      }
    }

    if (canQueue && r.status === 503) {
      if (bodyText === null && opts.body != null) return r;
      queueMutation(fullUrl.pathname + fullUrl.search, method, opts.headers, bodyText);
      return buildQueuedResponse(fullUrl.pathname, method, bodyText);
    }

    return r;
  } catch(e) {
    if (canQueue) {
      if (bodyText === null && opts.body != null) throw e;
      queueMutation(fullUrl.pathname + fullUrl.search, method, opts.headers, bodyText);
      return buildQueuedResponse(fullUrl.pathname, method, bodyText);
    }
    throw e;
  }
};

async function checkAuth() {
  updateLogoutButton();
  try {
    const r = await _origFetch(`${API}/auth/status`);
    const d = await r.json();
    authEnabled = !!d.auth_enabled;
    updateLogoutButton();
    if (d.auth_enabled && !authToken) {
      currentUserId = null;
      currentUserRole = null;
      updateLogoutButton();
      showLoginOverlay(d);
      return false;
    }
    if (d.auth_enabled && authToken) {
      // Verify token still valid
      const t = await _origFetch(`${API}/health`, { headers: authHeaders() });
      if (t.status === 401) {
        authToken = '';
        currentUserId = null;
        currentUserRole = null;
        localStorage.removeItem('dv_token');
        updateLogoutButton();
        showLoginOverlay(d);
        return false;
      }
      // Fetch current user info for ownership checks
      try {
        const mr = await _origFetch(`${API}/auth/me`, { headers: authHeaders() });
        const me = await mr.json();
        if (me.authenticated) {
          currentUserId = me.id;
          currentUserRole = me.role;
        }
      } catch(e) {}
    }
    if (!d.auth_enabled) {
      currentUserId = null;
      currentUserRole = null;
    }
    updateLogoutButton();
    return true;
  } catch(e) { return true; }
}

function showLoginOverlay(statusData) {
  document.getElementById('loginOverlay').style.display = 'flex';
  const regBtn = document.getElementById('btnRegisterToggle');
  if (regBtn) {
    const hasAuth = !!(statusData && statusData.auth_enabled);
    const regEnabled = !!(statusData && statusData.registration_enabled);
    const hasUsers = !!(statusData && statusData.has_users);
    const inviteMode = hasAuth && hasUsers && !regEnabled;
    // Show button when auth is not yet set up, registration is open, or invite codes are available
    regBtn.style.display = (!hasAuth || !hasUsers || regEnabled || inviteMode) ? '' : 'none';
    const i18nKey = inviteMode ? 'login.registerWithInvite' : 'login.register';
    regBtn.setAttribute('data-i18n', i18nKey);
    regBtn.innerHTML = t(i18nKey);
  }
  // Show invite code field only when registration is disabled (invite required)
  const inviteRow = document.getElementById('loginInviteCodeRow');
  if (inviteRow) {
    const inviteMode = !!(statusData && statusData.auth_enabled && statusData.has_users && !statusData.registration_enabled);
    inviteRow.style.display = inviteMode ? 'block' : 'none';
  }
  // Hide the register form when overlay opens
  const regForm = document.getElementById('loginRegisterForm');
  if (regForm) regForm.style.display = 'none';
}

function hideLoginOverlay() {
  document.getElementById('loginOverlay').style.display = 'none';
}

function base64urlToBuffer(base64url) {
  let s = base64url.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const raw = atob(s);
  const buf = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
  return buf.buffer;
}

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  bytes.forEach(b => binary += String.fromCharCode(b));
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function loginPasskey() {
  const btn = document.getElementById('btnLogin');
  btn.innerHTML = t('js.waitingPasskey');
  btn.disabled = true;
  try {
    const optResp = await _origFetch(`${API}/auth/login/options`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const data = await optResp.json();
    if (data.error) { showStatus('loginStatus', data.error, 'error'); btn.innerHTML = t('js.loginBtn'); btn.disabled = false; return; }
    const options = data.options;
    if (!options || !options.challenge) { showStatus('loginStatus', t('js.noChallenge'), 'error'); btn.innerHTML = t('js.loginBtn'); btn.disabled = false; return; }

    options.challenge = base64urlToBuffer(options.challenge);
    if (options.allowCredentials) {
      options.allowCredentials = options.allowCredentials.map(c => ({
        ...c, id: base64urlToBuffer(c.id)
      }));
    }

    const assertion = await navigator.credentials.get({ publicKey: options });

    const credential = {
      id: assertion.id,
      rawId: bufferToBase64url(assertion.rawId),
      response: {
        authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
        clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
        signature: bufferToBase64url(assertion.response.signature),
        userHandle: assertion.response.userHandle ? bufferToBase64url(assertion.response.userHandle) : null,
      },
      type: assertion.type,
      authenticatorAttachment: assertion.authenticatorAttachment,
    };

    const verResp = await _origFetch(`${API}/auth/login/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential })
    });
    const verData = await verResp.json();
    if (verData.token) {
      authToken = verData.token;
      localStorage.setItem('dv_token', authToken);
      updateLogoutButton();
      hideLoginOverlay();
      init();
    } else {
      showStatus('loginStatus', verData.error || t('js.loginFailed'), 'error');
    }
  } catch(e) {
    if (e.name === 'NotAllowedError') {
      showStatus('loginStatus', t('js.passkeyCancelled'), 'error');
    } else {
      showStatus('loginStatus', t('js.error', e.message), 'error');
    }
  }
  btn.innerHTML = t('js.loginBtn');
  btn.disabled = false;
}

async function registerPasskey() {
  const username = document.getElementById('registerUsername').value.trim() || 'admin';
  const credName = document.getElementById('registerCredName').value.trim() || 'Passkey';
  await _doRegisterPasskey(username, credName);
}

async function registerAdditionalPasskey() {
  const credName = document.getElementById('addCredName').value.trim() || 'Passkey';
  // Use the current user's username from the JWT token
  let username = 'admin';
  try {
    const me = await fetch(`${API}/auth/me`).then(r => r.json());
    if (me.username) username = me.username;
  } catch(e) {}
  await _doRegisterPasskey(username, credName);
}

async function _doRegisterPasskey(username, credName) {
  try {
    showStatus('authStatus', t('js.waitingPasskey'), 'info');

    const optResp = await _origFetch(`${API}/auth/register/options`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ username })
    });
    const { user_id, options } = await optResp.json();

    options.challenge = base64urlToBuffer(options.challenge);
    options.user.id = base64urlToBuffer(options.user.id);
    if (options.excludeCredentials) {
      options.excludeCredentials = options.excludeCredentials.map(c => ({
        ...c, id: base64urlToBuffer(c.id)
      }));
    }

    const attestation = await navigator.credentials.create({ publicKey: options });

    const credential = {
      id: attestation.id,
      rawId: bufferToBase64url(attestation.rawId),
      response: {
        attestationObject: bufferToBase64url(attestation.response.attestationObject),
        clientDataJSON: bufferToBase64url(attestation.response.clientDataJSON),
      },
      type: attestation.type,
      authenticatorAttachment: attestation.authenticatorAttachment,
    };

    const verResp = await _origFetch(`${API}/auth/register/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ user_id, username, credential_name: credName, credential })
    });
    const verData = await verResp.json();
    if (verData.token) {
      authToken = verData.token;
      localStorage.setItem('dv_token', authToken);
      updateLogoutButton();
      showStatus('authStatus', t('js.passkeyRegistered', credName), 'success');
      // Show recovery code if provided
      if (verData.recovery_code) {
        document.getElementById('recoveryCodeDisplay').textContent = verData.recovery_code;
        document.getElementById('recoveryCodeCard').style.display = 'block';
      }
      loadAuthSettings();
    } else {
      showStatus('authStatus', verData.error || t('js.registrationFailed'), 'error');
    }
  } catch(e) {
    if (e.name === 'NotAllowedError') {
      showStatus('authStatus', t('js.passkeyRegCancelled'), 'error');
    } else {
      showStatus('authStatus', t('js.error', e.message), 'error');
    }
  }
}

// ---------------------------------------------------------------------------
// Profile management
// ---------------------------------------------------------------------------

async function loadProfile() {
  try {
    const r = await fetch(`${API}/auth/me`);
    const me = await r.json();
    if (!me.authenticated) {
      // Auth disabled or not logged in — clear fields
      document.getElementById('profileUsername').value = '';
      document.getElementById('profileFirstName').value = '';
      document.getElementById('profileLastName').value = '';
      const img = document.getElementById('profileAvatarImg');
      const placeholder = document.getElementById('profileAvatarPlaceholder');
      const removeBtn = document.getElementById('profileAvatarRemoveBtn');
      if (img) { img.style.display = 'none'; img.src = ''; }
      if (placeholder) placeholder.style.display = '';
      if (removeBtn) removeBtn.style.display = 'none';
      return;
    }

    document.getElementById('profileUsername').value = me.username || '';
    document.getElementById('profileFirstName').value = me.first_name || '';
    document.getElementById('profileLastName').value = me.last_name || '';

    const img = document.getElementById('profileAvatarImg');
    const placeholder = document.getElementById('profileAvatarPlaceholder');
    const removeBtn = document.getElementById('profileAvatarRemoveBtn');
    if (me.avatar_url) {
      img.src = me.avatar_url;
      img.style.display = 'block';
      placeholder.style.display = 'none';
      removeBtn.style.display = 'inline-flex';
    } else {
      img.style.display = 'none';
      img.src = '';
      placeholder.style.display = '';
      removeBtn.style.display = 'none';
    }
    document.getElementById('profileUsernameError').style.display = 'none';
    document.getElementById('profileStatus').textContent = '';
  } catch(e) {}
}

// ── Profile submenu ───────────────────────────────────────────────────────────

let currentProfileSubmenu = 'general';

function switchProfileSubmenu(name) {
  currentProfileSubmenu = name;
  document.querySelectorAll('[data-profile-sub]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-profile-sub') === name);
  });
  document.querySelectorAll('#panel-profile .profile-sub-section').forEach(s => s.classList.remove('active'));
  const map = {
    general:       'profileSubGeneral',
    security:      'profileSubSecurity',
    preferences:   'profileSubPreferences',
    notifications: 'profileSubNotifications',
    apikeys:       'profileSubApiKeys',
    mcp:           'profileSubMcp',
  };
  const el = document.getElementById(map[name] || map.general);
  if (el) el.classList.add('active');
  if (name === 'general')       loadProfile();
  if (name === 'security')      loadAuthSettings();
  if (name === 'preferences')   loadDebugSettings(); // also loads showLocalTitleToggle
  if (name === 'notifications') initPushNotifications();
  if (name === 'apikeys')       loadApiKeys();
  if (name === 'mcp')           loadMcpLogs();
  if (name === 'preferences') {
    loadRatingCountryPicker();
  }
}

let currentAdminSubmenu = 'security';

const ADMIN_SECTIONS = ['adminSubSecurity','adminSubUsers','adminSubGroups','adminSubBackup','adminSubLogs','adminSubAdvanced'];

function switchAdminSubmenu(name) {
  currentAdminSubmenu = name;
  document.querySelectorAll('[data-admin-sub]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-admin-sub') === name);
  });
  const map = {
    security:  'adminSubSecurity',
    users:     'adminSubUsers',
    groups:    'adminSubGroups',
    backup:    'adminSubBackup',
    logs:      'adminSubLogs',
    advanced:  'adminSubAdvanced',
  };
  const targetId = map[name] || map.security;
  // Hide all admin sub-sections by ID (reliable, no CSS cascade dependency)
  ADMIN_SECTIONS.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  const target = document.getElementById(targetId);
  if (target) target.style.display = 'block';
  if (name === 'logs')      loadLogs();
  if (name === 'backup')    loadBackups();
  if (name === 'advanced')  loadDigitalSources();
}

async function loadAdminTab() {
  // Show submenu immediately — don't wait for async auth/settings fetches
  switchAdminSubmenu(currentAdminSubmenu || 'security');
  try {
    const me = await fetch(`${API}/auth/me`).then(r => r.json());
    const isAdmin = !me.authenticated || me.role === 'admin';
    if (!isAdmin) {
      switchTab('collection');
      return;
    }
    await loadAuthSettings();
    loadDebugSettings();
    loadMcpSettings();
    switchAdminSubmenu(currentAdminSubmenu || 'security');
  } catch(e) {
    switchTab('collection');
  }
}

// ── API key management ────────────────────────────────────────────────────────

async function loadApiKeys() {
  const card = document.getElementById('apiKeysCard');
  const list = document.getElementById('apiKeysList');
  if (!card || !currentUserId) return;
  card.style.display = '';
  try {
    const r = await fetch(`${API}/user/api-keys`, { headers: authHeaders() });
    if (!r.ok) { card.style.display = 'none'; return; }
    const keys = await r.json();
    if (!keys.length) {
      list.innerHTML = `<div style="font-size:0.82rem; color:var(--text-muted);">Nog geen API-sleutels aangemaakt.</div>`;
      return;
    }
    list.innerHTML = keys.map(k => `
      <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:8px; margin-bottom:8px;">
        <div>
          <div style="font-size:0.85rem; font-weight:500;">${k.label || '<span style="color:var(--text-muted);">Naamloos</span>'}</div>
          <div style="font-size:0.72rem; color:var(--text-muted);">Aangemaakt: ${(k.created_at||'').slice(0,10)}</div>
        </div>
        <button class="btn btn-danger" onclick="revokeApiKey(${k.id})" style="padding:5px 10px; font-size:0.74rem;">🗑 Intrekken</button>
      </div>
    `).join('');
  } catch(e) { card.style.display = 'none'; }
}

async function generateApiKey() {
  const label = (document.getElementById('apiKeyLabel').value || '').trim();
  const statusEl = document.getElementById('apiKeyStatus');
  const revealEl = document.getElementById('apiKeyReveal');
  const revealVal = document.getElementById('apiKeyRevealValue');
  try {
    const r = await fetch(`${API}/user/api-keys`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ label }),
    });
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    const data = await r.json();
    document.getElementById('apiKeyLabel').value = '';
    revealVal.textContent = data.key;
    revealEl.style.display = '';
    revealEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    await loadApiKeys();
    if (statusEl) statusEl.innerHTML = '';
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">✗ ${e.message}</span>`;
  }
}

function copyApiKey() {
  const val = document.getElementById('apiKeyRevealValue').textContent;
  navigator.clipboard.writeText(val).catch(() => {});
}

async function revokeApiKey(id) {
  const statusEl = document.getElementById('apiKeyStatus');
  try {
    const r = await fetch(`${API}/user/api-keys/${id}`, { method: 'DELETE', headers: authHeaders() });
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    await loadApiKeys();
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">✗ ${e.message}</span>`;
  }
}

async function loadMcpLogs() {
  const card = document.getElementById('mcpLogsCard');
  const list = document.getElementById('mcpLogsList');
  if (!card || !currentUserId) return;
  try {
    const r = await fetch(`${API}/user/mcp-logs?limit=50`, { headers: authHeaders() });
    if (!r.ok) { card.style.display = 'none'; return; }
    const logs = await r.json();
    card.style.display = '';
    if (!logs.length) {
      list.innerHTML = `<div style="text-align:center; padding:20px; color:var(--text-muted); font-family:inherit; font-size:0.84rem;">Nog geen MCP-activiteit geregistreerd.</div>`;
      return;
    }
    const levelColor = { error: 'var(--danger)', warn: 'var(--warning,#e8c547)', success: 'var(--success)', info: 'var(--text-muted)' };
    list.innerHTML = logs.map(l => {
      const ts = (l.timestamp || '').replace('T',' ').slice(0,19);
      const tool = (l.message || '').replace(/^Tool:\s*/i, '');
      const color = levelColor[l.level] || 'var(--text-muted)';
      const detail = l.detail ? `<div style="color:var(--text-muted); margin-top:3px; font-size:0.70rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${(l.detail||'').slice(0,120)}</div>` : '';
      return `<div style="padding:8px 10px; border-bottom:1px solid var(--border);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="color:${color}; font-weight:600;">${tool}</span>
          <span style="font-size:0.68rem; color:var(--text-muted); flex-shrink:0; margin-left:12px;">${ts}</span>
        </div>${detail}
      </div>`;
    }).join('');
  } catch(e) { card.style.display = 'none'; }
}

async function saveProfile() {
  const username = (document.getElementById('profileUsername').value || '').trim();
  const firstName = (document.getElementById('profileFirstName').value || '').trim();
  const lastName = (document.getElementById('profileLastName').value || '').trim();
  const errEl = document.getElementById('profileUsernameError');
  errEl.style.display = 'none';

  if (!username) {
    errEl.textContent = t('settings.profileUsernameRequired');
    errEl.style.display = 'block';
    return;
  }

  try {
    const r = await fetch(`${API}/auth/profile`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ username, first_name: firstName, last_name: lastName })
    });
    const d = await r.json();
    if (!r.ok) {
      if (d.field === 'username') {
        errEl.textContent = t('settings.profileUsernameTaken');
        errEl.style.display = 'block';
      } else {
        showStatus('profileStatus', d.error || 'Error', 'error');
      }
      return;
    }
    // Update token if re-issued
    if (d.token) {
      authToken = d.token;
      localStorage.setItem('authToken', d.token);
    }
    showStatus('profileStatus', t('settings.profileSaved'), 'success');
  } catch(e) {
    showStatus('profileStatus', t('js.error', e.message), 'error');
  }
}

async function uploadProfileAvatar(input) {
  if (!input.files || !input.files[0]) return;
  const file = input.files[0];
  if (file.size > 2 * 1024 * 1024) {
    showStatus('profileStatus', t('settings.profilePhotoTooLarge'), 'error');
    input.value = '';
    return;
  }
  const formData = new FormData();
  formData.append('avatar', file);
  try {
    const r = await fetch(`${API}/auth/profile/avatar`, { method: 'POST', body: formData });
    const d = await r.json();
    if (!r.ok) {
      showStatus('profileStatus', d.error || 'Error', 'error');
      return;
    }
    const img = document.getElementById('profileAvatarImg');
    img.src = d.avatar_url + '?t=' + Date.now();
    img.style.display = 'block';
    document.getElementById('profileAvatarPlaceholder').style.display = 'none';
    document.getElementById('profileAvatarRemoveBtn').style.display = 'inline-flex';
    showStatus('profileStatus', t('settings.profilePhotoUpdated'), 'success');
  } catch(e) {
    showStatus('profileStatus', t('js.error', e.message), 'error');
  }
  input.value = '';
}

async function removeProfileAvatar() {
  try {
    const r = await fetch(`${API}/auth/profile/avatar`, { method: 'DELETE' });
    if (!r.ok) return;
    document.getElementById('profileAvatarImg').style.display = 'none';
    document.getElementById('profileAvatarImg').src = '';
    document.getElementById('profileAvatarPlaceholder').style.display = '';
    document.getElementById('profileAvatarRemoveBtn').style.display = 'none';
    showStatus('profileStatus', t('settings.profilePhotoRemoved'), 'success');
  } catch(e) {}
}

async function loadAuthSettings() {
  try {
    const sr = await fetch(`${API}/auth/status`);
    const status = await sr.json();
    authEnabled = !!status.auth_enabled;
    const isAdmin = status.role === 'admin';
    updateLogoutButton();

    // Auth toggle: admin only
    const authToggleRow = document.getElementById('authToggle').closest('div');
    if (authToggleRow) authToggleRow.style.display = (isAdmin || !status.auth_enabled) ? '' : 'none';

    document.getElementById('authToggle').checked = status.auth_enabled;
    document.getElementById('authStatusBadge').textContent = status.auth_enabled ? t('js.authActive') : t('js.authOff');
    document.getElementById('authStatusBadge').style.color = status.auth_enabled ? 'var(--success)' : 'var(--text-muted)';

    if (status.has_credentials) {
      document.getElementById('authNoCredentials').style.display = 'none';
      document.getElementById('addPasskeySection').style.display = 'block';
    } else {
      document.getElementById('authNoCredentials').style.display = 'block';
      document.getElementById('addPasskeySection').style.display = 'none';
    }

    const cr = await fetch(`${API}/auth/credentials`);
    const creds = await cr.json();
    const list = document.getElementById('credentialsList');
    if (creds.length) {
      list.innerHTML = creds.map(c => `
        <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
          <div style="font-size:1.2rem;">🔑</div>
          <div style="flex:1;">
            <div style="font-weight:500; font-size:0.88rem;">${c.credential_name || 'Passkey'}</div>
            <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">
              ${c.username} · ${(c.created_at||'').slice(0,10)} · ${c.sign_count} ${t('js.logins')}
            </div>
          </div>
          <button class="btn btn-danger" style="padding:6px 10px; font-size:0.75rem;" onclick="deleteCredential('${c.id}')">✕</button>
        </div>
      `).join('');
    } else {
      list.innerHTML = '';
    }
    // Load admin panel if user is admin
    loadAdminPanel();

    // Show registration toggle for admin only
    const regToggleRow = document.getElementById('registrationToggleRow');
    if (regToggleRow) {
      if (status.auth_enabled && isAdmin) {
        regToggleRow.style.display = 'block';
        document.getElementById('registrationToggle').checked = !!status.registration_enabled;
      } else {
        regToggleRow.style.display = 'none';
      }
    }
  } catch(e) {}
}

async function deleteCredential(id) {
  if (!confirm(t('js.confirmDeletePasskey'))) return;
  await fetch(`${API}/auth/credentials/${encodeURIComponent(id)}`, { method: 'DELETE' });
  loadAuthSettings();
}

async function toggleAuth() {
  const enabled = document.getElementById('authToggle').checked;
  authEnabled = !!enabled;
  updateLogoutButton();
  const r = await fetch(`${API}/auth/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ enabled })
  });
  const d = await r.json();
  if (d.error) {
    showStatus('authStatus', d.error, 'error');
    document.getElementById('authToggle').checked = !enabled;
    authEnabled = !enabled;
    updateLogoutButton();
  } else {
    showStatus('authStatus', enabled ? t('js.authEnabled') : t('js.authDisabled'), 'success');
    authEnabled = !!enabled;
    updateLogoutButton();
  }
  loadAuthSettings();
}

// ── Recovery Login ────────────────────────────────────────────────────────────

function toggleRecoveryLogin() {
  const form = document.getElementById('recoveryLoginForm');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

async function recoveryLogin() {
  const username = document.getElementById('recoveryUsername').value.trim();
  const code = document.getElementById('recoveryCode').value.trim();
  if (!username || !code) { showStatus('loginStatus', t('login.recoveryFillIn'), 'error'); return; }
  try {
    const r = await _origFetch(`${API}/auth/recovery`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, recovery_code: code })
    });
    const d = await r.json();
    if (d.token) {
      authToken = d.token;
      localStorage.setItem('dv_token', authToken);
      updateLogoutButton();
      hideLoginOverlay();
      init();
      // Show new recovery code
      if (d.new_recovery_code) {
        setTimeout(() => {
          alert(t('login.newRecoveryAlert', d.new_recovery_code));
        }, 500);
      }
    } else {
      showStatus('loginStatus', d.error || t('login.recoveryFailed'), 'error');
    }
  } catch(e) {
    showStatus('loginStatus', `Fout: ${e.message}`, 'error');
  }
}

// ── Login: Registration ───────────────────────────────────────────────────────

function toggleLoginRegister() {
  const form = document.getElementById('loginRegisterForm');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

async function loginRegisterPasskey() {
  const username = document.getElementById('loginRegUsername').value.trim();
  const credName = document.getElementById('loginRegCredName').value.trim() || 'Passkey';
  const inviteCode = (document.getElementById('loginInviteCode') ? document.getElementById('loginInviteCode').value.trim() : '') || undefined;
  if (!username) { showStatus('loginStatus', t('login.registerFillIn'), 'error'); return; }
  const btn = document.getElementById('btnLoginRegister');
  btn.innerHTML = t('js.waitingPasskey');
  btn.disabled = true;
  try {
    const optResp = await _origFetch(`${API}/auth/register/options`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, invite_code: inviteCode })
    });
    const optData = await optResp.json();
    if (optData.error) { showStatus('loginStatus', optData.error, 'error'); btn.innerHTML = t('login.registerBtn'); btn.disabled = false; return; }
    const { user_id, options } = optData;

    options.challenge = base64urlToBuffer(options.challenge);
    options.user.id = base64urlToBuffer(options.user.id);
    if (options.excludeCredentials) {
      options.excludeCredentials = options.excludeCredentials.map(c => ({
        ...c, id: base64urlToBuffer(c.id)
      }));
    }

    const attestation = await navigator.credentials.create({ publicKey: options });

    const credential = {
      id: attestation.id,
      rawId: bufferToBase64url(attestation.rawId),
      response: {
        attestationObject: bufferToBase64url(attestation.response.attestationObject),
        clientDataJSON: bufferToBase64url(attestation.response.clientDataJSON),
      },
      type: attestation.type,
      authenticatorAttachment: attestation.authenticatorAttachment,
    };

    const verResp = await _origFetch(`${API}/auth/register/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id, username, credential_name: credName, invite_code: inviteCode, credential })
    });
    const verData = await verResp.json();
    if (verData.token) {
      authToken = verData.token;
      localStorage.setItem('dv_token', authToken);
      updateLogoutButton();
      hideLoginOverlay();
      init();
      if (verData.recovery_code) {
        setTimeout(() => {
          alert(t('login.newRecoveryAlert', verData.recovery_code));
        }, 500);
      }
    } else {
      showStatus('loginStatus', verData.error || t('js.registrationFailed'), 'error');
    }
  } catch(e) {
    if (e.name === 'NotAllowedError') {
      showStatus('loginStatus', t('js.passkeyRegCancelled'), 'error');
    } else {
      showStatus('loginStatus', t('js.error', e.message), 'error');
    }
  }
  btn.innerHTML = t('login.registerBtn');
  btn.disabled = false;
}

async function toggleRegistration() {
  const enabled = document.getElementById('registrationToggle').checked;
  const r = await fetch(`${API}/settings/registration`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ registration_enabled: enabled })
  });
  const d = await r.json();
  if (!r.ok) {
    // Revert checkbox on failure
    document.getElementById('registrationToggle').checked = !enabled;
    showStatus('authStatus', d.error || 'Error', 'error');
  } else {
    showStatus('authStatus', enabled ? t('js.registrationEnabled') : t('js.registrationDisabled'), 'success');
  }
}

// ---------------------------------------------------------------------------
// Admin: Invite Codes
// ---------------------------------------------------------------------------

async function createInviteCode() {
  const username = (document.getElementById('newInviteUsername').value || '').trim();
  if (!username) { showStatus('adminInviteStatus', t('login.registerFillIn'), 'error'); return; }
  try {
    const r = await fetch(`${API}/auth/invite`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ username })
    });
    const d = await r.json();
    if (!r.ok) { showStatus('adminInviteStatus', d.error || t('js.inviteCreateError'), 'error'); return; }
    document.getElementById('inviteCodeForUser').textContent = d.username;
    document.getElementById('inviteCodeDisplay').textContent = d.code;
    document.getElementById('inviteCodeExpiry').textContent = `${t('settings.inviteExpires')}: ${(d.expires_at||'').slice(0,16).replace('T',' ')} UTC`;
    document.getElementById('inviteCodeResult').style.display = 'block';
    document.getElementById('newInviteUsername').value = '';
    showStatus('adminInviteStatus', t('js.inviteCreated'), 'success');
    await loadInviteCodes();
  } catch(e) {
    showStatus('adminInviteStatus', t('js.error', e.message), 'error');
  }
}

function copyInviteCode() {
  const code = document.getElementById('inviteCodeDisplay').textContent;
  navigator.clipboard.writeText(code).then(() => {
    showStatus('adminInviteStatus', t('js.inviteCopied'), 'success');
  }).catch(() => {});
}

async function loadInviteCodes() {
  try {
    const r = await fetch(`${API}/auth/invite`, { headers: authHeaders() });
    if (!r.ok) return;
    const codes = await r.json();
    const list = document.getElementById('adminInviteList');
    if (!list) return;
    if (!codes.length) {
      list.innerHTML = `<div style="font-size:0.82rem; color:var(--text-muted);">${t('js.noInviteCodes')}</div>`;
      return;
    }
    const now = new Date();
    list.innerHTML = codes.map(c => {
      const isUsed = !!c.used_at;
      const isExpired = !isUsed && new Date(c.expires_at) < now;
      const statusLabel = isUsed ? `<span style="color:var(--text-muted)">${t('settings.inviteUsed')}</span>`
                        : isExpired ? `<span style="color:var(--danger)">${t('settings.inviteExpired')}</span>`
                        : `<span style="color:var(--success)">${t('settings.inviteActive')}</span>`;
      return `
        <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:6px; font-size:0.82rem;">
          <div style="flex:1;">
            <strong>${c.username}</strong>
            <span style="color:var(--text-muted); margin-left:8px;">${(c.expires_at||'').slice(0,16).replace('T',' ')} UTC</span>
            <span style="margin-left:8px;">${statusLabel}</span>
          </div>
          ${!isUsed ? `<button class="btn btn-danger" style="padding:4px 8px; font-size:0.75rem;" onclick="revokeInviteCode(${c.id})">${t('settings.inviteRevoke')}</button>` : ''}
        </div>`;
    }).join('');
  } catch(e) {}
}

async function revokeInviteCode(id) {
  try {
    const r = await fetch(`${API}/auth/invite/${id}`, { method: 'DELETE', headers: authHeaders() });
    if (r.ok) {
      showStatus('adminInviteStatus', t('js.inviteRevoked'), 'success');
      await loadInviteCodes();
    }
  } catch(e) {}
}

async function loadAdminPanel() {
  try {
    const me = await fetch(`${API}/auth/me`).then(r => r.json());
    const isAdmin = !me.authenticated || me.role === 'admin';
    const isMemberGroups = me.authenticated && me.role === 'MemberGroups';

    // Show/hide Admin button in meer menu
    const adminMenuBtn = document.getElementById('meerMenuAdmin');
    if (adminMenuBtn) adminMenuBtn.style.display = isAdmin ? '' : 'none';

    if (!isAdmin && !isMemberGroups) {
      document.getElementById('adminUsersCard').style.display = 'none';
      document.getElementById('adminGroupsCard').style.display = 'none';
      document.getElementById('myGroupsCard').style.display = 'none';
      return;
    }
    if (isAdmin) {
      document.getElementById('adminGroupsCard').style.display = 'block';
      document.getElementById('myGroupsCard').style.display = 'block';
      if (me.authenticated) {
        document.getElementById('adminUsersCard').style.display = 'block';
        await loadAdminUsers();
        document.getElementById('adminInviteCard').style.display = 'block';
        await loadInviteCodes();
      } else {
        document.getElementById('adminUsersCard').style.display = 'none';
        document.getElementById('adminInviteCard').style.display = 'none';
      }
      await loadAdminGroups();
      await loadMyGroups();
    } else {
      // MemberGroups
      document.getElementById('adminGroupsCard').style.display = 'none';
      document.getElementById('adminUsersCard').style.display = 'none';
      document.getElementById('adminInviteCard').style.display = 'none';
      document.getElementById('myGroupsCard').style.display = 'block';
      await loadMyGroups();
    }
  } catch(e) {
    document.getElementById('adminUsersCard').style.display = 'none';
    document.getElementById('adminGroupsCard').style.display = 'none';
    document.getElementById('adminInviteCard').style.display = 'none';
    const mgc = document.getElementById('myGroupsCard');
    if (mgc) mgc.style.display = 'none';
  }
}

async function loadAdminUsers() {
  try {
    const r = await fetch(`${API}/auth/users`);
    const users = await r.json();
    const list = document.getElementById('adminUsersList');
    list.innerHTML = users.map(u => {
      const roleIcon = u.role === 'admin' ? '👑' : u.role === 'MemberGroups' ? '🔑' : '👤';
      const nextRole = u.role === 'admin' ? 'MemberGroups' : u.role === 'MemberGroups' ? 'user' : 'admin';
      const nextIcon = nextRole === 'admin' ? '👑' : nextRole === 'MemberGroups' ? '🔑' : '👤';
      return `
      <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
        <div style="font-size:1.2rem;">${roleIcon}</div>
        <div style="flex:1;">
          <div style="font-weight:500; font-size:0.88rem;">${u.display_name || u.username}
            <span class="tag" style="font-size:0.65rem; margin-left:6px;">${u.role}</span>
          </div>
          <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">
            ${u.username} · ${(u.created_at||'').slice(0,10)} · ${t('js.passkeyCount', u.credential_count)}
          </div>
        </div>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="resetUserPasskey('${u.id}','${u.username}')" title="Reset passkey">🔑</button>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="toggleUserRole('${u.id}','${u.role}')" title="Wissel rol (${u.role} \u2192 ${nextRole})">${nextIcon}</button>
          <button class="btn btn-danger" style="padding:6px 10px; font-size:0.7rem;" onclick="deleteUser('${u.id}','${u.username}')" title="Verwijder">✕</button>
        </div>
      </div>`;
    }).join('');
  } catch(e) {}
}

async function deleteUser(id, name) {
  if (!confirm(t('js.confirmDeleteUser', name))) return;
  await fetch(`${API}/auth/users/${encodeURIComponent(id)}`, { method: 'DELETE' });
  loadAdminUsers();
  loadAuthSettings();
}

async function toggleUserRole(id, currentRole) {
  const newRole = currentRole === 'admin' ? 'MemberGroups' : currentRole === 'MemberGroups' ? 'user' : 'admin';
  await fetch(`${API}/auth/users/${encodeURIComponent(id)}/role`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: newRole })
  });
  loadAdminUsers();
}

async function resetUserPasskey(id, name) {
  if (!confirm(t('js.confirmResetPasskey', name))) return;
  await fetch(`${API}/auth/users/${encodeURIComponent(id)}/reset-passkey`, { method: 'POST' });
  loadAdminUsers();
  loadAuthSettings();
}

