// ── Admin: Group Management ───────────────────────────────────────────────────

async function loadAdminGroups() {
  try {
    const r = await fetch(`${API}/groups`);
    const groups = await r.json();
    const list = document.getElementById('adminGroupsList');
    if (!groups.length) {
      list.innerHTML = '<div style="color:var(--text-muted); font-size:0.85rem;">' + t('js.noGroups') + '</div>';
      return;
    }
    list.innerHTML = groups.map(g => `
      <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
        <div style="font-size:1.2rem;">📁</div>
        <div style="flex:1;">
          <div style="font-weight:500; font-size:0.88rem;">${g.name}</div>
          <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">
            ${t('js.groupStatsFull', g.member_count, g.movie_count, g.created_by_username || '?')}
          </div>
        </div>
        <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="manageGroupMembers(${g.id},'${g.name}')" title="${t('js.manageMembersBtn')}">👥</button>
        <button class="btn btn-danger" style="padding:6px 10px; font-size:0.7rem;" onclick="deleteGroup(${g.id},'${g.name}')" title="${t('js.deleteTitle')}">✕</button>
      </div>
    `).join('');
  } catch(e) {}
}

async function createGroup() {
  const name = document.getElementById('newGroupName').value.trim();
  if (!name) return;
  const r = await fetch(`${API}/groups`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  document.getElementById('newGroupName').value = '';
  loadAdminGroups();
}

async function deleteGroup(id, name) {
  if (!confirm(t('js.confirmDeleteGroup', name))) return;
  await fetch(`${API}/groups/${id}`, { method: 'DELETE' });
  loadAdminGroups();
}

async function manageGroupMembers(groupId, groupName) {
  const [membersResp, usersResp] = await Promise.all([
    fetch(`${API}/groups/${groupId}/members`),
    fetch(`${API}/auth/users`)
  ]);
  const members = await membersResp.json();
  const allUsers = await usersResp.json();
  const memberIds = new Set(members.map(m => m.id));
  const nonMembers = allUsers.filter(u => !memberIds.has(u.id));

  let html = `<h3 style="margin:0 0 16px 0; font-size:1rem;">${t('js.membersOf', groupName)}</h3>`;
  if (members.length) {
    html += members.map(m => `
      <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:6px;">
        <span style="flex:1; font-size:0.85rem;">${m.display_name || m.username}</span>
        <button class="btn btn-danger" style="padding:4px 8px; font-size:0.7rem;" onclick="removeGroupMember(${groupId},'${m.id}','${groupName}')">✕</button>
      </div>
    `).join('');
  } else {
    html += `<div style="color:var(--text-muted); font-size:0.85rem; margin-bottom:10px;">${t('js.noMembers')}</div>`;
    html += `<div style="display:flex; gap:8px; align-items:center; margin-top:12px;">
      <select id="addMemberSelect" style="flex:1; padding:8px; background:var(--surface2); color:var(--text); border:1px solid var(--border); border-radius:6px;">
        ${nonMembers.map(u => `<option value="${u.id}">${u.display_name || u.username}</option>`).join('')}
      </select>
      <button class="btn btn-primary" style="padding:8px 14px; font-size:0.8rem;" onclick="addGroupMember(${groupId},'${groupName}')">${t('js.addMemberBtn')}</button>
    </div>`;
  }
  html += `<button class="btn btn-secondary" style="margin-top:16px; width:100%; justify-content:center;" onclick="loadAdminGroups(); this.closest('.card').querySelector('#groupMemberPanel').remove();">${t('js.close')}</button>`;

  // Replace groups list content with member management
  const panel = document.createElement('div');
  panel.id = 'groupMemberPanel';
  panel.style.cssText = 'padding:16px; background:var(--surface); border:1px solid var(--border); border-radius:8px; margin-top:12px;';
  panel.innerHTML = html;
  const existing = document.getElementById('groupMemberPanel');
  if (existing) existing.remove();
  document.getElementById('adminGroupsList').appendChild(panel);
}

async function addGroupMember(groupId, groupName) {
  const userId = document.getElementById('addMemberSelect').value;
  await fetch(`${API}/groups/${groupId}/members`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId })
  });
  manageGroupMembers(groupId, groupName);
}

async function removeGroupMember(groupId, userId, groupName) {
  await fetch(`${API}/groups/${groupId}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' });
  manageGroupMembers(groupId, groupName);
}

// ── MemberGroups: Mijn Groepen ────────────────────────────────────────────────

async function loadMyGroups() {
  const list = document.getElementById('myGroupsList');
  if (!list) return;
  try {
    const r = await fetch(`${API}/groups`);
    const groups = await r.json();
    const owned = groups.filter(g => g.my_role === 'owner');
    const member = groups.filter(g => g.my_role !== 'owner');
    if (!owned.length && !member.length) {
      list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('js.noMyGroups')}</div>`;
      return;
    }
    let html = '';
    if (owned.length) {
      html += `<div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em; margin-bottom:8px;">${t('js.myGroupsOwned')}</div>`;
      html += owned.map(g => `
        <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
          <div style="font-size:1.2rem;">👑</div>
          <div style="flex:1;">
            <div style="font-weight:500; font-size:0.88rem;">${g.name}</div>
            <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">${t('js.groupStats', g.member_count, g.movie_count)}</div>
          </div>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="manageMyGroupMembers(${g.id},'${g.name}')" title="${t('js.manageMembersTitle')}">👥</button>
          <button class="btn btn-danger" style="padding:6px 10px; font-size:0.7rem;" onclick="deleteMyGroup(${g.id},'${g.name}')" title="${t('js.deleteTitle')}">✕</button>
        </div>
      `).join('');
    }
    if (member.length) {
      html += `<div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em; margin-bottom:8px; margin-top:16px;">${t('js.myGroupsMember')}</div>`;
      html += member.map(g => `
        <div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">
          <div style="font-size:1.2rem;">📁</div>
          <div style="flex:1;">
            <div style="font-weight:500; font-size:0.88rem;">${g.name}</div>
            <div style="font-family:'DM Mono',monospace; font-size:0.72rem; color:var(--text-muted);">${t('js.groupStatsByOwner', g.created_by_username || '?', g.movie_count)}</div>
          </div>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.7rem;" onclick="leaveMyGroup(${g.id},'${g.name}')">${t('js.leaveGroup')}</button>
        </div>
      `).join('');
    }
    list.innerHTML = html;
    // Manage panel passthrough
    const existing = document.getElementById('myGroupMemberPanel');
    if (existing) existing.remove();
  } catch(e) {
    list.innerHTML = `<div style="color:var(--danger); font-size:0.85rem;">${t('js.myGroupsLoadError')}</div>`;
  }
}

async function createMyGroup() {
  const nameEl = document.getElementById('myNewGroupName');
  const name = nameEl.value.trim();
  if (!name) return;
  const r = await fetch(`${API}/groups`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
  const d = await r.json();
  if (d.error) { showStatus('myGroupStatus', d.error, 'error'); return; }
  nameEl.value = '';
  showStatus('myGroupStatus', t('js.myGroupCreated', name), 'success');
  await loadMyGroups();
  await _loadGroupFilter();
}

async function deleteMyGroup(id, name) {
  if (!confirm(t('js.confirmDeleteMyGroup', name))) return;
  await fetch(`${API}/groups/${id}`, { method: 'DELETE' });
  await loadMyGroups();
  await _loadGroupFilter();
  filterMovies();
}

async function leaveMyGroup(groupId, groupName) {
  if (!confirm(t('js.confirmLeaveGroup', groupName))) return;
  await fetch(`${API}/groups/${groupId}/members/${encodeURIComponent(currentUserId)}`, { method: 'DELETE' });
  await loadMyGroups();
  await _loadGroupFilter();
  filterMovies();
}

async function manageMyGroupMembers(groupId, groupName) {
  const members = await fetch(`${API}/groups/${groupId}/members`).then(r => r.json());
  let html = `<div style="font-weight:500; margin-bottom:12px; font-size:0.9rem;">${t('js.membersOf', groupName)}</div>`;
  if (members.length) {
    html += members.map(m => {
      const isSelf = m.id === currentUserId;
      const isOwner = m.group_role === 'owner';
      return `
        <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:var(--surface); border:1px solid var(--border); border-radius:6px; margin-bottom:6px;">
          <span style="font-size:0.9rem;">${isOwner ? '👑' : '👤'}</span>
          <span style="flex:1; font-size:0.85rem;">${m.display_name || m.username}${isSelf ? t('js.selfIndicator') : ''}</span>
          ${!isOwner ? `<button class="btn btn-danger" style="padding:4px 8px; font-size:0.7rem;" onclick="removeMyGroupMember(${groupId},'${m.id}','${groupName}')">✕</button>` : ''}
        </div>`;
    }).join('');
  } else {
    html += `<div style="color:var(--text-muted); font-size:0.85rem; margin-bottom:10px;">${t('js.noMembers')}</div>`;
  }
  html += `
    <div style="border-top:1px solid var(--border); margin-top:14px; padding-top:14px;">
      <div style="font-size:0.8rem; font-weight:500; margin-bottom:8px;">${t('js.inviteUser')}</div>
      <div style="display:flex; gap:8px; align-items:center;">
        <input type="text" id="inviteUsernameInput_${groupId}" placeholder="${t('js.usernamePlaceholder')}"
               style="flex:1; padding:8px; background:var(--surface2); color:var(--text); border:1px solid var(--border); border-radius:6px; font-size:0.84rem;">
        <button class="btn btn-primary" style="padding:8px 14px; font-size:0.8rem;" onclick="sendGroupInvite(${groupId},'${groupName}')">${t('js.inviteBtn')}</button>
      </div>
      <div id="inviteStatus_${groupId}" class="status-msg" style="margin-top:6px;"></div>
    </div>
    <button class="btn btn-secondary" style="margin-top:14px; width:100%; justify-content:center;" onclick="document.getElementById('myGroupMemberPanel').remove(); loadMyGroups();">${t('js.close')}</button>
  `;
  const panel = document.createElement('div');
  panel.id = 'myGroupMemberPanel';
  panel.style.cssText = 'padding:16px; background:var(--surface); border:1px solid var(--border); border-radius:8px; margin-top:12px;';
  panel.innerHTML = html;
  const existing = document.getElementById('myGroupMemberPanel');
  if (existing) existing.remove();
  document.getElementById('myGroupsList').appendChild(panel);
}

async function removeMyGroupMember(groupId, userId, groupName) {
  await fetch(`${API}/groups/${groupId}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' });
  manageMyGroupMembers(groupId, groupName);
}

async function sendGroupInvite(groupId, groupName) {
  const input = document.getElementById(`inviteUsernameInput_${groupId}`);
  const statusEl = document.getElementById(`inviteStatus_${groupId}`);
  const username = input.value.trim();
  if (!username) return;
  const r = await fetch(`${API}/groups/${groupId}/invite`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username })
  });
  const d = await r.json();
  if (d.error) {
    showStatus(`inviteStatus_${groupId}`, d.error, 'error');
  } else {
    showStatus(`inviteStatus_${groupId}`, t('js.inviteSent', username), 'success');
    input.value = '';
  }
}

// ── Invite notifications ──────────────────────────────────────────────────────

async function loadInviteNotifications() {
  try {
    const r = await fetch(`${API}/invites`);
    if (!r.ok) return;
    const invites = await r.json();
    const bell = document.getElementById('inviteBell');
    const badge = document.getElementById('inviteBadge');
    if (!bell) return;
    if (invites.length > 0) {
      bell.style.display = '';
      badge.style.display = '';
      badge.textContent = invites.length;
    } else {
      bell.style.display = 'none';
    }
  } catch(e) {}
}

async function openInvitePanel() {
  const panel = document.getElementById('invitePanel');
  const list = document.getElementById('invitePanelList');
  panel.style.display = 'flex';
  list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('general.loading')}</div>`;
  try {
    const r = await fetch(`${API}/invites`);
    const invites = await r.json();
    if (!invites.length) {
      list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem; text-align:center; padding:20px;">${t('js.noInvites')}</div>`;
      return;
    }
    list.innerHTML = invites.map(inv => `
      <div style="background:var(--surface2); border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:10px;">
        <div style="font-weight:500; margin-bottom:4px;">📁 ${inv.group_name}</div>
        <div style="font-size:0.8rem; color:var(--text-muted); margin-bottom:12px;">
          ${t('js.inviteFrom', `<strong>${inv.inviter_display_name || inv.inviter_username}</strong>`)} · ${(inv.created_at||'').slice(0,10)}
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-primary" style="flex:1; justify-content:center;" onclick="respondInvite(${inv.id},'accept')">${t('js.inviteAccept')}</button>
          <button class="btn btn-secondary" style="flex:1; justify-content:center;" onclick="respondInvite(${inv.id},'decline')">${t('js.inviteDecline')}</button>
        </div>
      </div>
    `).join('');
  } catch(e) {
    list.innerHTML = `<div style="color:var(--danger); font-size:0.85rem;">${t('js.inviteLoadError')}</div>`;
  }
}

function closeInvitePanel() {
  document.getElementById('invitePanel').style.display = 'none';
}

async function respondInvite(inviteId, action) {
  const r = await fetch(`${API}/invites/${inviteId}/${action}`, { method: 'POST' });
  const d = await r.json();
  if (action === 'accept' && d.status === 'accepted') {
    await loadCollection();
    await _loadGroupFilter();
  }
  await loadInviteNotifications();
  await openInvitePanel();
}

// ── Lists: Watchlist & Watched ────────────────────────────────────────────────

function switchListsSubmenu(sub) {
  document.querySelectorAll('[data-lists-sub]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-lists-sub') === sub);
  });
  document.querySelectorAll('#panel-lists .profile-sub-section').forEach(s => s.classList.remove('active'));
  if (sub === 'watchlist') {
    const el = document.getElementById('listsSubWatchlist');
    if (el) el.classList.add('active');
    loadWatchlist();
  } else if (sub === 'watchhistory') {
    const el = document.getElementById('listsSubWatchHistory');
    if (el) el.classList.add('active');
    loadWatchHistory();
  }
}

async function loadWatchlist() {
  const grid  = document.getElementById('watchlistGrid');
  const empty = document.getElementById('watchlistEmpty');
  if (!grid) return;
  grid.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem; padding:20px;">${t('general.loading')}</div>`;
  try {
    const r = await fetch(`${API}/watchlist`, { headers: authHeaders() });
    if (!r.ok) { grid.innerHTML = ''; return; }
    const movies = await r.json();
    if (!movies.length) {
      grid.innerHTML = '';
      if (empty) empty.style.display = '';
      return;
    }
    if (empty) empty.style.display = 'none';
    grid.innerHTML = movies.map(m => {
      const src = posterSrc(m);
      const imgHtml = src
        ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
        : '<div class="no-img">🎬</div>';
      const watched = m.last_watched ? `<div class="watched-check" title="${t('js.watchedOnDate', m.last_watched)}">✓</div>` : '';
      return `
      <div class="movie-card" data-id="${m.id}" onclick="openMovieDetail(${m.id})" style="position:relative;">
        ${watched}
        <div class="movie-card-poster">${imgHtml}
          <div class="movie-card-format">${m.format || '4K'}</div>
        </div>
        <div class="movie-card-info">
          <div class="movie-card-title">${m.title}</div>
          <div class="movie-card-year">${m.year || '—'}</div>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    grid.innerHTML = '';
  }
}

async function loadWatchHistory() {
  const list  = document.getElementById('watchHistoryList');
  const empty = document.getElementById('watchHistoryEmpty');
  if (!list) return;
  list.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem; padding:20px;">${t('general.loading')}</div>`;
  try {
    const r = await fetch(`${API}/watch-history`, { headers: authHeaders() });
    if (!r.ok) { list.innerHTML = ''; return; }
    const entries = await r.json();
    if (!entries.length) {
      list.innerHTML = '';
      if (empty) empty.style.display = '';
      return;
    }
    if (empty) empty.style.display = 'none';
    // Group by date
    const byDate = {};
    entries.forEach(e => {
      const d = e.watched_at.slice(0,10);
      if (!byDate[d]) byDate[d] = [];
      byDate[d].push(e);
    });
    list.innerHTML = Object.keys(byDate).map(date => {
      const label = _formatDate(date);
      const rows = byDate[date].map(e => {
        const src = posterSrc(e);
        const img = src
          ? `<img src="${src}" onerror="this.outerHTML='<div class=\\'no-img-sm\\'>🎬</div>'">`
          : '<div class="no-img-sm">🎬</div>';
        return `<div class="watch-history-row" onclick="openMovieDetail(${e.movie_id})">
          ${img}
          <div>
            <div class="whr-title">${e.title}</div>
            <div class="whr-meta">${e.year || '—'} · ${e.format || '—'}</div>
          </div>
        </div>`;
      }).join('');
      return `<div class="watch-history-day">
        <div class="watch-history-day-label">${label}</div>
        ${rows}
      </div>`;
    }).join('');
  } catch(e) {
    list.innerHTML = '';
  }
}

// Current movie watchlist/watched state (set when modal opens)
let _modalMovieOnWatchlist = false;
let _modalMovieLastWatched = null;

async function toggleWatchlistModal() {
  if (!currentMovieId) return;
  const btn = document.getElementById('btnWatchlistModal');
  if (_modalMovieOnWatchlist) {
    await fetch(`${API}/watchlist/${currentMovieId}`, { method: 'DELETE', headers: authHeaders() });
    _modalMovieOnWatchlist = false;
  } else {
    await fetch(`${API}/watchlist/${currentMovieId}`, { method: 'POST', headers: authHeaders() });
    _modalMovieOnWatchlist = true;
  }
  _updateWatchlistBtn();
  // Update allMovies cache
  const m = allMovies.find(x => x.id === currentMovieId);
  if (m) m.on_watchlist = _modalMovieOnWatchlist;
}

function _updateWatchlistBtn() {
  const btn = document.getElementById('btnWatchlistModal');
  if (!btn) return;
  btn.textContent = _modalMovieOnWatchlist ? t('modal.inWatchlist') : t('modal.watchlist');
  btn.style.color = _modalMovieOnWatchlist ? 'var(--accent)' : '';
  btn.style.borderColor = _modalMovieOnWatchlist ? 'rgba(232,197,71,.5)' : '';
}

function _updateWatchedBtn() {
  const btn = document.getElementById('btnWatchedModal');
  if (!btn) return;
  if (_modalMovieLastWatched) {
    btn.textContent = t('modal.watchedOn', _modalMovieLastWatched.slice(0,10));
    btn.style.color = '#4caf50';
    btn.style.borderColor = 'rgba(76,175,80,.4)';
  } else {
    btn.textContent = t('modal.watched');
    btn.style.color = '';
    btn.style.borderColor = '';
  }
}

function toggleWatchedMenu(e) {
  e.stopPropagation();
  const popup = document.getElementById('watchedPopup');
  if (!popup) return;
  const isVisible = popup.style.display !== 'none';
  if (isVisible) {
    popup.style.display = 'none';
    return;
  }
  popup.style.display = 'block';
  // Set today as default
  const inp = document.getElementById('watchedDateInput');
  if (inp) inp.value = new Date().toISOString().slice(0,10);
  // Load existing history entries
  if (currentMovieId) _refreshWatchedHistoryPopup(currentMovieId);
  // Close on click OUTSIDE the popup
  setTimeout(() => {
    const closeHandler = (ev) => {
      if (!popup.contains(ev.target)) {
        popup.style.display = 'none';
        document.removeEventListener('click', closeHandler);
      }
    };
    document.addEventListener('click', closeHandler);
  }, 0);
}

async function _refreshWatchedHistoryPopup(movieId) {
  const section = document.getElementById('watchedHistorySection');
  const container = document.getElementById('watchedHistoryEntries');
  if (!section || !container) return [];
  const r = await fetch(`${API}/watched/${movieId}`, { headers: authHeaders() });
  if (!r.ok) { section.style.display = 'none'; return []; }
  const entries = await r.json();
  if (!entries.length) { section.style.display = 'none'; return []; }
  section.style.display = '';
  container.innerHTML = entries.map(e => {
    const d = e.watched_at.slice(0,10);
    const label = _formatDate(d);
    return `<div class="watched-history-entry">
      <span>${label}</span>
      <button class="del-btn" onclick="_deleteWatchedEntry(${e.id}, ${movieId})" title="Verwijderen">\u2715</button>
    </div>`;
  }).join('');
  return entries;
}

async function _deleteWatchedEntry(entryId, movieId) {
  await fetch(`${API}/watched/entry/${entryId}`, { method: 'DELETE', headers: authHeaders() });
  await _refreshWatchedHistoryPopup(movieId);
  // Refresh the movie's last_watched in allMovies
  const r2 = await fetch(`${API}/watched/${movieId}`, { headers: authHeaders() });
  const remaining = r2.ok ? await r2.json() : [];
  const newLastWatched = remaining.length ? remaining[0].watched_at.slice(0,10) : null;
  _modalMovieLastWatched = newLastWatched;
  _updateWatchedBtn();
  const m = allMovies.find(x => x.id === movieId);
  if (m) m.last_watched = newLastWatched;
}

function _formatDate(dateStr) {
  if (!dateStr) return '';
  const months = t('months.short').split(',');
  const [y, mo, d] = dateStr.split('-');
  return `${parseInt(d)} ${months[parseInt(mo)-1]} ${y}`;
}

async function markWatchedModal(type) {
  if (!currentMovieId) return;
  let date;
  const now = new Date();
  if (type === 'today') {
    date = now.toISOString().slice(0,10);
  } else if (type === 'yesterday') {
    const y = new Date(now); y.setDate(y.getDate() - 1);
    date = y.toISOString().slice(0,10);
  } else {
    date = document.getElementById('watchedDateInput').value;
    if (!date) return;
  }
  // Close popup immediately
  document.getElementById('watchedPopup').style.display = 'none';
  const r = await fetch(`${API}/watched/${currentMovieId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ watched_at: date })
  });
  if (!r.ok) return;
  const rd = await r.json().catch(() => ({}));

  if (rd && rd.queued) {
    // Offline: update UI optimistically without server round-trip
    _modalMovieLastWatched = date;
    _updateWatchedBtn();
    const m = allMovies.find(x => x.id === currentMovieId);
    if (m) m.last_watched = date;
    // Show the new entry in the history popup with a pending indicator
    const section = document.getElementById('watchedHistorySection');
    const container = document.getElementById('watchedHistoryEntries');
    if (section && container) {
      section.style.display = '';
      const label = _formatDate(date);
      const row = `<div class="watched-history-entry"><span>${label}</span><span style="font-size:0.7rem;color:var(--text-muted)"> ⏳</span></div>`;
      container.innerHTML = row + container.innerHTML;
    }
  } else {
    // Online: re-fetch from server for accurate history
    let entries = [];
    try { entries = await _refreshWatchedHistoryPopup(currentMovieId); } catch(e) {}
    _modalMovieLastWatched = entries.length ? entries[0].watched_at.slice(0, 10) : date;
    _updateWatchedBtn();
    const m = allMovies.find(x => x.id === currentMovieId);
    if (m) m.last_watched = _modalMovieLastWatched;
  }
  // Refresh Watch History page if currently open
  if (document.getElementById('listsSubWatchHistory')?.classList.contains('active')) {
    loadWatchHistory();
  }
}

async function unmarkWatchedModal() {
  // Kept for backwards compat but no longer called from UI
  if (!currentMovieId) return;
  document.getElementById('watchedPopup').style.display = 'none';
  await fetch(`${API}/watched/entry`, { method: 'DELETE' });
}

async function bulkAddToWatchlist() {
  if (!selectedIds.size) return;
  const ids = Array.from(selectedIds);
  await fetch(`${API}/watchlist/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ movie_ids: ids })
  });
  // Update local cache
  ids.forEach(id => {
    const m = allMovies.find(x => x.id === id);
    if (m) m.on_watchlist = true;
  });
  showStatus('bulkGroupStatus', t('js.bulkWatchlistAdded', ids.length), 'success');
  setTimeout(() => toggleSelectMode(), 1200);
}

