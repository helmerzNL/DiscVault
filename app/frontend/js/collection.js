// ── Collection ────────────────────────────────────────────────────────────────

// Country flag as inline SVG image (flagcdn.com), works on all platforms including Windows
function _flagImg(code) {
  const lc = (code || '').toLowerCase();
  return `<img src="/flags/${lc}.svg" width="20" height="15" alt="${lc.toUpperCase()}" style="border-radius:2px;vertical-align:middle;flex-shrink:0;">`;
}

// ── Edition / Compare state ───────────────────────────────────────────────────
let compareMode     = false;
let compareData     = null;
let activeCompareTab = 'both';

// Fetch compare data for digital badges + modal play links (non-blocking)
async function loadDigitalBadgeData() {
  if (!userHasDigital) return;
  try {
    const r = await fetch(`${API}/collection/compare`);
    if (r.ok) {
      compareData = await r.json();
      renderGrid(getCurrentMovies());
    }
  } catch(e) { /* silently ignore — badges are optional */ }
}

let groupEditionsEnabled = localStorage.getItem('dv_group_editions') === 'true';
let showDigitalBadges    = localStorage.getItem('dv_digital_badges') === 'true';
let digitalBadgeFilter   = localStorage.getItem('dv_digital_badge_filter') || 'all';
let collectorsMode       = localStorage.getItem('dv_collectors_mode') === 'true';

// Apply body class immediately so CSS .collectors-only rules take effect before render
if (collectorsMode) document.body.classList.add('collectors-mode');
let activeEditionFilter  = false;

// ── Selection mode ────────────────────────────────────────────────────────────
let selectMode = false;
let selectedIds = new Set();

function toggleSelectMode() {
  selectMode = !selectMode;
  selectedIds.clear();
  const btn = document.getElementById('btnSelectMode');
  const toolbar = document.getElementById('bulkToolbar');
  const grid = document.getElementById('moviesGrid');
  if (selectMode) {
    btn.classList.add('btn-primary');
    btn.classList.remove('btn-secondary');
    toolbar.classList.add('visible');
    grid.classList.add('select-mode');
  } else {
    btn.classList.remove('btn-primary');
    btn.classList.add('btn-secondary');
    toolbar.classList.remove('visible');
    grid.classList.remove('select-mode');
  }
  updateBulkCount();
  renderGrid(getCurrentMovies());
}

function getCurrentMovies() {
  const searchInput = document.getElementById('searchInput');
  const q = searchInput ? searchInput.value.toLowerCase() : '';
  const groupFilter = document.getElementById('groupFilter');
  const activeGroup = groupFilter ? groupFilter.value : '';
  return allMovies.filter(m => {
    if (m._isNested) return false; // nested editions only accessible via drawer/detail
    const matchesFormat  = !activeFormat || m.format === activeFormat;
    const matchesGroup   = !activeGroup ||
      (activeGroup === '_mine' ? (m.owner_id === currentUserId) : (m.group_ids || []).includes(parseInt(activeGroup)));
    const matchesEdition = !activeEditionFilter || (m.edition_group_id != null || m._is_super_group || m._is_collection);
    const matchesQuery   = movieMatchesSearch(m, q);
    return matchesFormat && matchesGroup && matchesEdition && matchesQuery;
  });
}

function movieMatchesSearch(m, q) {
  if (!q) return true;
  const people = Array.isArray(m.searchPeople)
    ? m.searchPeople.join(' ')
    : (m.search_people || '');
  return [
    m.barcode,
    m.title,
    m.original_title,
    m.director,
    m.actor,
    m.genre,
    m.box_set,
    m.studios,
    m.distributor,
    people,
  ].some(value => (value || '').toString().toLowerCase().includes(q));
}

function canEditMovie(m) {
  if (!authEnabled || !currentUserId) return true;
  if (currentUserRole === 'admin') return true;
  // If user has custom roles, enforce permission-based access
  if (userCustomRoles && userCustomRoles.length > 0) {
    if (hasPermission('collection.edit_all')) return true;
    if (hasPermission('collection.edit_own') && String(m.owner_id) === String(currentUserId)) return true;
    return false;
  }
  // Legacy: no custom role — owner can always edit their own movies
  return String(m.owner_id) === String(currentUserId);
}

function toggleCard(id) {
  const movie = allMovies.find(m => m.id === id);
  if (movie && !canEditMovie(movie)) return;
  if (selectedIds.has(id)) {
    selectedIds.delete(id);
  } else {
    selectedIds.add(id);
  }
  // Toggle .selected on the card element without full re-render
  const card = document.querySelector(`.movie-card[data-id="${id}"]`);
  if (card) card.classList.toggle('selected', selectedIds.has(id));
  updateBulkCount();
}

function selectAll() {
  getCurrentMovies().filter(canEditMovie).forEach(m => selectedIds.add(m.id));
  getCurrentMovies().forEach(m => {
    const card = document.querySelector(`.movie-card[data-id="${m.id}"]`);
    if (card) card.classList.toggle('selected', selectedIds.has(m.id));
  });
  updateBulkCount();
}

function deselectAll() {
  selectedIds.clear();
  document.querySelectorAll('.movie-card[data-id]').forEach(c => c.classList.remove('selected'));
  updateBulkCount();
}

function updateBulkCount() {
  const n = selectedIds.size;
  document.getElementById('bulkCount').textContent =
    n === 0 ? t('bulk.noSelection') : t('js.moviesSelected', n);
  document.getElementById('btnSelectAll').textContent =
    selectedIds.size === getCurrentMovies().length ? t('bulk.deselect') : t('bulk.selectAll');
}

function renderGrid(movies) {
  const grid = document.getElementById('moviesGrid');
  if (!movies.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1">
        <span class="big-icon">📀</span>
        <h3>${t('js.noMoviesFound')}</h3>
        <p>${t('js.addFirstDisc')}</p>
      </div>`;
    return;
  }

  grid.innerHTML = movies.map(m => {
    const src       = posterSrc(m);
    const imgHtml   = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
      : '<div class="no-img">🎬</div>';
    const isSelected = selectedIds.has(m.id);
    const ownable = canEditMovie(m);
    const clickHandler = selectMode
      ? (ownable ? `toggleCard(${m.id})` : `openMovieDetail(${m.id})`)
      : `openMovieDetail(${m.id})`;
    const safeTitle = (m.title || '').replace(/'/g, "\\'");
    const showDelete = !selectMode && debugModeEnabled && ownable;

    const isGroupCard = !!(m._is_group && m.editions_count > 1);
    const isCollectionCard = !!m._is_collection;
    const isBoxSetCard = !!(m._is_super_group && !m._is_collection);
    const displayTitle = (isGroupCard || isCollectionCard || isBoxSetCard) ? (m._group_title || m.title) : m.title;
    const displayYear  = isGroupCard ? `${m.editions_count} ${t('js.editions', 'editions')}` : (m.year || '\u2014');

    // Edition badge
    const edType = m.edition_type || 'standard';
    const editionBadge = (!isGroupCard && edType && edType !== 'standard')
      ? `<div class="movie-card-edition-badge" title="${t('edition.' + edType.replace('_',''), edType)}">${_editionShortLabel(edType, m.custom_edition_label)}</div>`
      : '';

    // Edition stack badge — only in collectors mode
    const stackBadge = (collectorsMode && !isGroupCard && m.editions_count > 1)
      ? `<div class="movie-card-stack-badge" onclick="event.stopPropagation(); openEditionGroupView(${m.id})">${_stackBadgeLabel(m)}</div>`
      : '';

    // Group indicator — only in collectors mode
    const groupIndicator = (collectorsMode && !groupEditionsEnabled && m.edition_group_id)
      ? `<div class="movie-card-group-indicator" title="${t('js.partOfVault', 'Part of a vault')}">🗂</div>`
      : '';

    // Format label: for group/collection/boxset cards show type label, otherwise format
    let formatLabel;
    if (isCollectionCard) {
      formatLabel = `<div class="movie-card-format group-count-badge">Collection</div>`;
    } else if (isBoxSetCard) {
      formatLabel = `<div class="movie-card-format group-count-badge">Box Set</div>`;
    } else if (isGroupCard) {
      formatLabel = `<div class="movie-card-format group-count-badge">Vault</div>`;
    } else {
      formatLabel = `<div class="movie-card-format">${m.format || '4K'}</div>`;
    }

    // Digital badge (Plex/Jellyfin) — only shown when user has digital.view permission
    let digitalBadge = '';
    if (showDigitalBadges && compareData && userHasDigital) {
      // Check per-group restriction: if userDigitalGroups is set, only show when viewing an allowed group
      const gf = document.getElementById('groupFilter');
      const activeGroup = gf ? gf.value : '';
      const groupOk = !userDigitalGroups ||
        !activeGroup || activeGroup === '_mine' ||
        userDigitalGroups.includes(parseInt(activeGroup));
      if (groupOk) {
        const match = (compareData.physical_and_digital || []).find(e => e.movie && e.movie.id === m.id);
        const matches = _digitalMatches(match);
        if (matches.length) {
          const filtered = digitalBadgeFilter === 'all'
            ? matches
            : matches.filter(x => x.sourceType === digitalBadgeFilter);
          // Deduplicate by source_type so we get one badge per platform
          const types = [...new Set(filtered.map(x => x.sourceType))];
          if (types.length) {
            const badges = types.map(st => {
              const names = filtered.filter(x => x.sourceType === st).map(x => x.sourceName).join(', ');
              const logo = st === 'plex'
                ? '<svg viewBox="0 0 24 24" width="14" height="14" fill="#E5A00D"><path d="M3.987 8.409c-.96 0-1.587.28-2.12.933v-.72H0v8.88s.038.018.127.037c.138.03.821.187 1.331-.249.441-.377.542-.814.542-1.318v-1.283c.533.573 1.147.813 2 .813 1.84 0 3.253-1.493 3.253-3.48 0-2.12-1.36-3.613-3.266-3.613Zm16.748 5.595.406.591c.391.614.894.906 1.492.908.621-.012 1.064-.562 1.226-.755 0 0-.307-.27-.686-.72-.517-.614-1.214-1.755-1.24-1.803l-1.198 1.779Zm-3.205-1.955c0-2.08-1.52-3.64-3.52-3.64s-3.467 1.587-3.467 3.573a3.48 3.48 0 0 0 3.507 3.52c1.413 0 2.626-.84 3.253-2.293h-2.04l-.093.093c-.427.4-.72.533-1.227.533-.787 0-1.373-.506-1.453-1.266h4.986c.04-.214.054-.307.054-.52Zm-7.671-.219c0 .769.11 1.701.868 2.722l.056.069c-.306.526-.742.88-1.248.88-.399 0-.814-.211-1.138-.579a2.177 2.177 0 0 1-.538-1.441V6.409H9.86l-.001 5.421Zm9.283 3.46h-2.39l2.247-3.332-2.247-3.335h2.39l2.248 3.335-2.248 3.332Zm1.593-1.286Zm-17.162-.342c-.933 0-1.68-.773-1.68-1.72s.76-1.666 1.68-1.666c.92 0 1.68.733 1.68 1.68 0 .946-.733 1.706-1.68 1.706Zm18.361-1.974L24 8.622h-2.391l-.87 1.293 1.195 1.773Zm-9.404-.466c.16-.706.72-1.133 1.493-1.133.773 0 1.373.467 1.507 1.133h-3Z"/></svg>'
                : '<svg viewBox="0 0 24 24" width="14" height="14" fill="#00A4DC"><path d="M12 .002C8.826.002-1.398 18.537.16 21.666c1.56 3.129 22.14 3.094 23.682 0C25.384 18.573 15.177 0 12 0zm7.76 18.949c-1.008 2.028-14.493 2.05-15.514 0C3.224 16.9 9.92 4.755 12.003 4.755c2.081 0 8.77 12.166 7.759 14.196zM12 9.198c-1.054 0-4.446 6.15-3.93 7.189.518 1.04 7.348 1.027 7.86 0 .511-1.027-2.874-7.19-3.93-7.19z"/></svg>';
              return `<div class="movie-card-digital-badge-icon" title="${names}">${logo}</div>`;
            }).join('');
            digitalBadge = `<div class="movie-card-digital-badge">${badges}</div>`;
          }
        }
      }
    }

    return `
    <div class="movie-card${isGroupCard ? ' edition-group-card' : ''}${isSelected ? ' selected' : ''}${selectMode && !ownable ? ' not-owned' : ''}" data-id="${m.id}" onclick="${clickHandler}">
      ${showDelete ? `<button class="movie-card-delete" onclick="event.stopPropagation(); quickDelete(${m.id}, '${safeTitle}')">✕</button>` : ''}
      ${!isGroupCard && m.on_watchlist ? `<div class="watchlist-dot" title="${t('js.onWatchlist')}"></div>` : ''}
      ${!isGroupCard && m.last_watched ? `<div class="watched-check" title="${t('js.watchedOn', m.last_watched.slice(0,10))}">✓</div>` : ''}
      <div class="movie-card-poster">
        ${imgHtml}
        ${formatLabel}
        ${editionBadge}${stackBadge}${groupIndicator}${digitalBadge}
      </div>
      <div class="movie-card-info">
        <div class="movie-card-title">${displayTitle}</div>
        <div class="movie-card-year${isGroupCard ? ' group-edition-count' : ''}">${displayYear}</div>
      </div>
      ${(m.editions_count > 1) ? `<div class="movie-card-editions-drawer" id="edDrawer_${m.id}" style="display:none;"></div>` : ''}
    </div>`;
  }).join('');
}

function filterMovies() {
  const movies = getCurrentMovies();
  renderGrid(movies);
  _renderHeaderStats();
  const fc = document.getElementById('filterCount');
  if (fc) {
    fc.textContent = t('js.filterCount', movies.length);
    fc.style.display = '';
  }
}

function getSearchMovies() {
  const input = document.getElementById('searchPageInput');
  const q = input ? input.value.toLowerCase().trim() : '';
  if (!q) return [];
  return allMovies.filter(m => movieMatchesSearch(m, q));
}

function renderSearchGrid(movies) {
  const grid = document.getElementById('searchMoviesGrid');
  if (!grid) return;
  const query = (document.getElementById('searchPageInput')?.value || '').trim();

  if (!query) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1">
        <span class="big-icon">🔎</span>
        <h3>${t('search.emptyTitle')}</h3>
        <p>${t('search.emptyDesc')}</p>
      </div>`;
    return;
  }

  if (!movies.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1">
        <span class="big-icon">📭</span>
        <h3>${t('js.noMoviesFound')}</h3>
        <p>${t('js.noResultsFor', query)}</p>
      </div>`;
    return;
  }

  grid.innerHTML = movies.map(m => {
    const src = posterSrc(m);
    const imgHtml = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
      : '<div class="no-img">🎬</div>';
    const safeTitle = (m.title || '').replace(/'/g, "\\'");
    const showDelete = debugModeEnabled;
    return `
    <div class="movie-card" data-id="${m.id}" onclick="openMovieDetail(${m.id})">
      ${showDelete ? `<button class="movie-card-delete" onclick="event.stopPropagation(); quickDelete(${m.id}, '${safeTitle}')">✕</button>` : ''}
      <div class="movie-card-poster">
        ${imgHtml}
        <div class="movie-card-format">${m.format || '4K'}</div>
      </div>
      <div class="movie-card-info">
        <div class="movie-card-title">${m.title}</div>
        <div class="movie-card-year">${m.year || '—'}</div>
      </div>
    </div>`;
  }).join('');
}

function filterSearchMovies() {
  renderSearchGrid(getSearchMovies());
}

// ── Bulk actions ──────────────────────────────────────────────────────────────

async function showBulkGroupAssign() {
  const panel = document.getElementById('bulkGroupPanel');
  const container = document.getElementById('bulkGroupCheckboxes');
  panel.style.display = 'block';
  container.innerHTML = `<span style="color:var(--text-muted); font-size:0.82rem;">${t('general.loading')}</span>`;
  try {
    const r = await fetch(`${API}/groups`);
    const groups = await r.json();
    if (!groups.length) {
      container.innerHTML = '<span style="color:var(--text-muted); font-size:0.82rem;">' + t('js.noGroups') + '</span>';
      return;
    }
    // Count how many selected movies are already in each group
    const selMovies = allMovies.filter(m => selectedIds.has(m.id));
    container.innerHTML = groups.map(g => {
      const inGroup = selMovies.filter(m => (m.group_ids || []).includes(g.id)).length;
      const badge = inGroup > 0
        ? `<span style="font-size:0.7rem; color:var(--accent); opacity:0.8;">(${inGroup}/${selMovies.length})</span>`
        : '';
      return `
      <label style="display:flex; align-items:center; gap:6px; padding:6px 12px; background:var(--surface2); border:1px solid ${inGroup === selMovies.length ? 'var(--accent)' : 'var(--border)'}; border-radius:6px; cursor:pointer; font-size:0.84rem; white-space:nowrap;">
        <input type="checkbox" class="bulk-group-cb" value="${g.id}" style="accent-color:var(--accent); width:15px; height:15px;"${inGroup === selMovies.length ? ' checked' : ''}>
        ${g.name} ${badge}
      </label>`;
    }).join('');
  } catch(e) {
    container.innerHTML = `<span style="color:var(--danger);">${t('js.groupsLoadError')}</span>`;
  }
}

async function bulkAssignGroups() {
  const ids = [...selectedIds];
  if (!ids.length) return;
  const allCbs = [...document.querySelectorAll('.bulk-group-cb')];
  const checkedIds = allCbs.filter(cb => cb.checked).map(cb => parseInt(cb.value));
  const uncheckedIds = allCbs.filter(cb => !cb.checked).map(cb => parseInt(cb.value));

  if (!checkedIds.length && !uncheckedIds.length) return;

  try {
    showStatus('bulkGroupStatus', '<span class="spinner"></span> ' + t('bulk.assigning'), 'info');

    // Add to checked groups
    if (checkedIds.length) {
      await fetch(`${API}/movies/bulk/groups`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ movie_ids: ids, group_ids: checkedIds })
      });
    }
    // Remove from unchecked groups
    if (uncheckedIds.length) {
      await fetch(`${API}/movies/bulk/groups`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ movie_ids: ids, group_ids: uncheckedIds })
      });
    }

    // Update local cache
    for (const m of allMovies) {
      if (selectedIds.has(m.id)) {
        let gids = new Set(m.group_ids || []);
        checkedIds.forEach(g => gids.add(g));
        uncheckedIds.forEach(g => gids.delete(g));
        m.group_ids = [...gids];
      }
    }
    showStatus('bulkGroupStatus', t('bulk.assignDone', ids.length), 'success');
    setTimeout(() => {
      document.getElementById('bulkGroupPanel').style.display = 'none';
      filterMovies();
    }, 1000);
  } catch(e) {
    showStatus('bulkGroupStatus', t('js.error', e.message), 'error');
  }
}

// ── Bulk container (Vault / Box-Set / Collection) assignment ─────────────────

async function showBulkContainerAssign() {
  const panel = document.getElementById('bulkContainerPanel');
  if (!panel) { console.error('[DV] bulkContainerPanel not found'); return; }
  panel.style.display = 'block';
  const groupPanel = document.getElementById('bulkGroupPanel');
  if (groupPanel) groupPanel.style.display = 'none';
  const sel = document.getElementById('bulkContainerSelect');
  if (!sel) { console.error('[DV] bulkContainerSelect not found'); return; }
  sel.innerHTML = '<option value="">Laden…</option>';
  try {
    const [egR, colR] = await Promise.all([
      fetch(`${API}/edition-groups`),
      fetch(`${API}/collections`)
    ]);
    if (!egR.ok) throw new Error(`Edition-groups API: ${egR.status}`);
    if (!colR.ok) throw new Error(`Collections API: ${colR.status}`);
    const egs  = await egR.json();
    const cols = await colR.json();
    if (!Array.isArray(egs))  throw new Error('Edition-groups response is not an array');
    if (!Array.isArray(cols)) throw new Error('Collections response is not an array');
    const vaults  = egs.filter(g => !_isBoxSetGroup(g));
    const boxsets = egs.filter(_isBoxSetGroup);
    let html = `<option value="">${t('bulk.containerPlaceholder', '-- Kies een container --')}</option>`;
    if (vaults.length) {
      html += `<optgroup label="Vault">`;
      vaults.forEach(g => { html += `<option value="vault:${g.id}">${escHtml(g.title)}</option>`; });
      html += `</optgroup>`;
    }
    if (boxsets.length) {
      html += `<optgroup label="Box Set">`;
      boxsets.forEach(g => { html += `<option value="boxset:${g.id}">${escHtml(g.title)}</option>`; });
      html += `</optgroup>`;
    }
    if (cols.length) {
      html += `<optgroup label="${t('bulk.directCollectionGroup', 'Collection (directe films)')}">`;
      cols.forEach(c => { html += `<option value="col:${c.id}">${escHtml(c.title)}</option>`; });
      html += `</optgroup>`;
    }
    sel.innerHTML = html;
  } catch(e) {
    console.error('[DV] showBulkContainerAssign error:', e);
    sel.innerHTML = `<option value="">Fout: ${escHtml(e.message)}</option>`;
  }
}

async function bulkAssignContainer() {
  const ids = [...selectedIds];
  if (!ids.length) return;
  const sel = document.getElementById('bulkContainerSelect');
  if (!sel || !sel.value) {
    showStatus('bulkContainerStatus', t('bulk.selectOneContainer'), 'error');
    return;
  }
  const [type, idStr] = sel.value.split(':');
  const targetId = parseInt(idStr);
  const targetLabel = sel.options[sel.selectedIndex].text;
  try {
    showStatus('bulkContainerStatus', '<span class="spinner"></span> ' + t('bulk.assigning'), 'info');
    let r;
    if (type === 'vault') {
      r = await fetch(`${API}/edition-groups/${targetId}/members`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ movie_ids: ids })
      });
    } else if (type === 'boxset') {
      r = await fetch(`${API}/movies/bulk/boxset`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ movie_ids: ids, super_group_id: targetId })
      });
    } else {
      r = await fetch(`${API}/movies/bulk/collection`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ movie_ids: ids, collection_id: targetId })
      });
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    // Update local cache
    for (const m of allMovies) {
      if (selectedIds.has(m.id)) {
        if (type === 'vault') m.edition_group_id = targetId;
        else if (type === 'boxset') m.super_group_id = targetId;
        else m.collection_id = targetId;
      }
    }
    showStatus('bulkContainerStatus', t('bulk.containerAssigned', ids.length, targetLabel), 'success');
    setTimeout(() => {
      document.getElementById('bulkContainerPanel').style.display = 'none';
      loadCollection();
    }, 1200);
  } catch(e) {
    showStatus('bulkContainerStatus', t('js.error', e.message), 'error');
  }
}

async function bulkDelete() {
  const ids = [...selectedIds];
  if (!ids.length) return;
  if (!confirm(t('js.confirmBulkDelete', ids.length))) return;

  showProgress(t('js.deleting'), ids.length);
  try {
    const visibleById = new Map(getCurrentMovies().map(m => [m.id, m]));
    const items = ids.map(id => {
      const m = visibleById.get(id) || allMovies.find(x => x.id === id) || {};
      if (m._is_super_group && m._parent_group_id) {
        return { type: 'box_set', id: m._parent_group_id, representative_id: id };
      }
      if (m._is_group && m.edition_group_id) {
        return { type: 'vault', id: m.edition_group_id, representative_id: id };
      }
      if (m._is_collection && m._collection_id) {
        return { type: 'collection', id: m._collection_id, representative_id: id };
      }
      return { type: 'movie', id };
    });
    const r = await fetch(`${API}/movies/bulk-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, items })
    });
    const d = await r.json();
    allMovies = allMovies.filter(m => !selectedIds.has(m.id));
    selectedIds.clear();
    setProgress(ids.length, ids.length);
    if (d.queued) {
      finishProgress(t('js.queuedDelete', ids.length));
    } else {
      finishProgress(t('js.deleted', d.deleted));
    }
    filterMovies();
    loadCollection();
    loadStats();
    updateBulkCount();
  } catch(e) {
    finishProgress(t('js.error', e.message), true);
  }
}

async function bulkRefresh() {
  const ids = [...selectedIds];
  if (!ids.length) return;

  showProgress(t('js.fetchingMetadata', ids.length), ids.length);

  let updated = 0;
  let skipped = 0;
  let errors = 0;
  const errorDetails = [];

  _bulkCancelled = false;
  _bulkAbortController = new AbortController();
  try {
    const r = await fetch(`${API}/movies/bulk-refresh?stream=1`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, fetch_posters: true }),
      signal: _bulkAbortController.signal
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);

    const reader = r.body.getReader();
    _bulkReader = reader;
    const decoder = new TextDecoder();
    let buf = '';

    try {
      while (!_bulkCancelled) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          let d;
          try { d = JSON.parse(line); } catch { continue; }
          if (d.type === 'progress') {
            setProgress(d.current, d.total, d.title);
          } else if (d.type === 'done') {
            updated = d.updated || 0;
            skipped = d.skipped || 0;
            errors  = d.errors  || 0;
            if (Array.isArray(d.error_details)) errorDetails.push(...d.error_details);
          }
        }
      }
    } finally {
      _bulkReader = null;
      try { reader.releaseLock(); } catch(_) {}
    }

    if (_bulkCancelled) {
      finishProgress(t('js.refreshCancelled', updated, skipped), false);
    } else {
      const errStr = errorDetails.length
        ? '\n' + t('js.refreshErrors', errors) + ':\n' + errorDetails.join('\n')
        : '';
      finishProgress(t('js.refreshResult', updated, skipped) + errStr);
    }
    await loadCollection();
    filterMovies();
    loadStats();
  } catch(e) {
    _bulkReader = null;
    if (_bulkCancelled || e.name === 'AbortError') {
      finishProgress(t('js.refreshCancelled', updated, skipped), false);
      await loadCollection(); filterMovies(); loadStats();
    } else {
      finishProgress(t('js.error', e.message), true);
    }
  }
}

// ── Progress overlay helpers ──────────────────────────────────────────────────

let _bulkAbortController = null;
let _bulkReader = null;
let _bulkCancelled = false;

function showProgress(title, total) {
  document.getElementById('progressTitle').textContent = title;
  document.getElementById('progressBar').style.width = '0%';
  document.getElementById('progressLabel').textContent = `0 / ${total}`;
  document.getElementById('progressSubtitle').textContent = '';
  document.getElementById('progressResult').style.display = 'none';
  document.getElementById('progressCloseBtn').style.display = 'none';
  const cancelBtn = document.getElementById('progressCancelBtn');
  if (cancelBtn) cancelBtn.style.display = '';
  document.getElementById('bulkProgressOverlay').classList.add('visible');
}

function setProgress(done, total, subtitle) {
  const pct = total ? Math.round((done / total) * 100) : 100;
  document.getElementById('progressBar').style.width = pct + '%';
  document.getElementById('progressLabel').textContent = `${done} / ${total}`;
  if (subtitle !== undefined) document.getElementById('progressSubtitle').textContent = subtitle;
}

function finishProgress(message, isError = false) {
  const result = document.getElementById('progressResult');
  result.style.display = 'block';
  result.style.color = isError ? 'var(--danger)' : 'var(--success)';
  result.style.whiteSpace = 'pre-line';
  result.textContent = message;
  document.getElementById('progressCloseBtn').style.display = 'inline-flex';
  document.getElementById('progressBar').style.width = '100%';
  const cancelBtn = document.getElementById('progressCancelBtn');
  if (cancelBtn) cancelBtn.style.display = 'none';
  _bulkAbortController = null;
}

function closeProgress() {
  document.getElementById('bulkProgressOverlay').classList.remove('visible');
}

function cancelBulkRefresh() {
  _bulkCancelled = true;
  if (_bulkReader)          { try { _bulkReader.cancel(); } catch(_) {} }
  if (_bulkAbortController) { _bulkAbortController.abort(); }
}

function setFormatFilter(format, btn) {
  activeFormat = format;
  document.querySelectorAll('.filter-btn:not(#btnEditionFilter)').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterMovies();
}

async function quickDelete(id, title) {
  if (!confirm(t('js.confirmDelete', title))) return;
  await fetch(`${API}/movies/${id}`, { method: 'DELETE' });
  allMovies = allMovies.filter(m => m.id !== id);
  filterMovies();
  filterSearchMovies();
  loadStats();
}

// ── Movie Detail Modal ────────────────────────────────────────────────────────
let _currentEditionGroupPrimaryId = null;
let _currentEditionGroupId = null; // actual edition_group_id (group table id)
let _egViewStack = []; // navigation stack for nested group views
let _currentSuperGroup = null;
let _currentCollection = null;
let _currentCollectionData = null;

/* ── Collection view ─────────────────────────────────────────────────────── */
function openCollectionView(movie) {
  _currentCollection = movie;
  _currentSuperGroup = null;
  _currentEditionGroupPrimaryId = null;
  _currentEditionGroupId = null;
  _currentEgGroupData = null;
  _currentCollectionData = null;

  // Inject loose movies into allMovies so openMovieDetail can find them
  (movie._loose_movies || []).forEach(lm => {
    if (!allMovies.some(m => m.id === lm.id))
      allMovies.push({ ...lm, _isNested: true });
  });

  document.getElementById('egPanelTitle').textContent = movie._group_title || movie.title || '';
  const boxSets = movie._box_sets || [];
  const vaults  = movie._vaults || [];
  const loose   = movie._loose_movies || [];
  const parts = [];
  if (boxSets.length) parts.push(`${boxSets.length} box set${boxSets.length > 1 ? 's' : ''}`);
  if (vaults.length)  parts.push(`${vaults.length} vault${vaults.length > 1 ? 's' : ''}`);
  if (loose.length)   parts.push(`${loose.length} film${loose.length > 1 ? 's' : ''}`);
  document.getElementById('egPanelSubtitle').textContent = parts.join(' · ') || '';

  const grid = document.getElementById('egPanelGrid');
  // Box Set cards
  const boxSetHtml = boxSets.map(bs => {
    const src = posterSrc(bs);
    const imgHtml = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
      : '<div class="no-img">🎬</div>';
    const count = bs.editions_count || bs._sub_group_count || 0;
    return `
      <div class="eg-edition-card" onclick="openBoxSetFromCollection(${bs._parent_group_id})">
        <div class="eg-edition-poster">
          ${imgHtml}
          <div class="eg-edition-fmt">${count}×</div>
          <div class="eg-edition-type-label">Box Set</div>
        </div>
        <div class="eg-edition-info">${bs._group_title || bs.title || ''}</div>
      </div>`;
  });
  // Vault cards
  const vaultHtml = vaults.map(v => {
    const src = posterSrc(v);
    const imgHtml = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
      : '<div class="no-img">🎬</div>';
    const count = v.editions_count || 0;
    return `
      <div class="eg-edition-card" onclick="openVaultFromCollection(${v.id})">
        <div class="eg-edition-poster">
          ${imgHtml}
          <div class="eg-edition-fmt">${count}×</div>
          <div class="eg-edition-type-label">Vault</div>
        </div>
        <div class="eg-edition-info">${v._group_title || v.title || ''}</div>
      </div>`;
  });
  // Loose movie cards
  const looseHtml = loose.map(lm => {
    const src = posterSrc(lm);
    const imgHtml = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
      : '<div class="no-img">🎬</div>';
    return `
      <div class="eg-edition-card" onclick="openMovieDetail(${lm.id}, true)">
        <div class="eg-edition-poster">
          ${imgHtml}
          <div class="eg-edition-fmt">${lm.format || ''}</div>
        </div>
        <div class="eg-edition-info">${lm.title || ''} ${lm.year ? '(' + lm.year + ')' : ''}</div>
      </div>`;
  });
  grid.innerHTML = [...boxSetHtml, ...vaultHtml, ...looseHtml].join('');
  _populateEgHero(movie, 'Collection');
  _populateEgManageSection(movie);
  switchEgTab('members');

  // Load collection detail from API for backdrop/description
  const colId = movie._collection_id;
  if (colId) {
    fetch(`${API}/collections/${colId}`)
      .then(r => r.ok ? r.json() : null)
      .then(c => {
        if (!c) return;
        _currentCollectionData = c;
        if (c.backdrop) _applyEgBackdrop(c.backdrop, movie);
        if (c.description) {
          const descEl = document.getElementById('egPanelDescription');
          if (descEl) descEl.textContent = c.description;
        }
        _populateEgManageSection(movie);
      });
  }

  window.scrollTo({ top: 0, behavior: 'smooth' });
  switchTabDirect('edition-group');
  if (movie._collection_id) _pushRoute(`/collection/${movie._collection_id}`);
}

function openBoxSetFromCollection(parentGroupId) {
  // Find the box set card from the collection's _box_sets
  const bs = (_currentCollection._box_sets || []).find(b => b._parent_group_id === parentGroupId);
  if (!bs) return;
  _egViewStack.push({ type: 'collection', movie: _currentCollection });
  // Open it as a super group view
  openSuperGroupView(bs);
}

async function openVaultById(groupId, fallbackMovieId) {
  try {
    const r = await fetch(`${API}/edition-groups/${groupId}`);
    if (!r.ok) return;
    const g = await r.json();
    const members = g.members || [];
    const primaryId = (members[0] && members[0].id) || fallbackMovieId || groupId;
    openEditionGroupView(primaryId, {
      id: primaryId,
      title: g.title,
      edition_group_id: groupId,
      _group_title: g.title,
      editions: members,
      editions_count: members.length,
      _container_poster_file: g.poster_file || '',
      backdrop: g.backdrop || '',
      description: g.description || '',
    });
  } catch(e) {}
}

async function openBoxSetById(groupId) {
  try {
    const r = await fetch(`${API}/edition-groups/${groupId}`);
    if (!r.ok) return;
    const g = await r.json();
    const looseMovies = g.loose_movies || g.members || [];
    openSuperGroupView({
      id: (g.loose_movies && g.loose_movies[0] && g.loose_movies[0].id) || groupId,
      _is_super_group: true,
      _parent_group_id: groupId,
      _group_title: g.title,
      title: g.title,
      editions_count: looseMovies.length,
      _loose_movies: looseMovies,
      _sub_groups: g.child_groups || [],
      _container_poster_file: g.poster_file || '',
      backdrop: g.backdrop || '',
      description: g.description || '',
    });
  } catch(e) {}
}

async function openCollectionById(collectionId) {
  try {
    const r = await fetch(`${API}/collections/${collectionId}`);
    if (!r.ok) return;
    const c = await r.json();
    const vaults = (c.edition_groups || []).filter(g => g.group_type !== 'boxset');
    const boxSets = (c.edition_groups || []).filter(g => g.group_type === 'boxset');
    openCollectionView({
      id: collectionId,
      _is_collection: true,
      _collection_id: collectionId,
      _group_title: c.title,
      title: c.title,
      _vaults: vaults.map(v => ({
        ...v,
        id: (c.eg_movies || []).find(m => m.edition_group_id === v.id)?.id || v.id,
        edition_group_id: v.id,
        _group_title: v.title,
      })),
      _box_sets: boxSets.map(bs => ({
        ...bs,
        _parent_group_id: bs.id,
        _group_title: bs.title,
      })),
      _loose_movies: c.loose_movies || [],
      backdrop: c.backdrop || '',
      description: c.description || '',
      _container_poster_file: c.poster_file || '',
    });
  } catch(e) {}
}

function openVaultFromCollection(movieId) {
  _egViewStack.push({ type: 'collection', movie: _currentCollection });
  // Find the vault card from the collection's _vaults
  const vault = (_currentCollection._vaults || []).find(v => v.id === movieId);
  openEditionGroupView(movieId, vault);
}

function openSuperGroupView(movie) {
  _currentSuperGroup = movie;
  _currentCollection = null;
  _currentEditionGroupPrimaryId = null;
  _currentEditionGroupId = null;
  _currentCollectionData = null;
  _currentEgGroupData = null;

  // Inject loose movies into allMovies so openMovieDetail can find them
  (movie._loose_movies || []).forEach(lm => {
    if (!allMovies.some(m => m.id === lm.id))
      allMovies.push({ ...lm, _isNested: true });
  });

  document.getElementById('egPanelTitle').textContent = movie._group_title || movie.title || '';
  const subCnt  = movie._sub_group_count || 0;
  const looseCnt = (movie._loose_movies || []).length;
  const totalEditions = movie.editions_count || 0;
  document.getElementById('egPanelSubtitle').textContent =
    [subCnt ? `${subCnt} vault${subCnt !== 1 ? 's' : ''}` : null,
     looseCnt ? `${looseCnt} film${looseCnt !== 1 ? 's' : ''}` : null,
     `${totalEditions} ${t('js.editions', 'editions')}`].filter(Boolean).join(' \u00b7 ');
  const grid = document.getElementById('egPanelGrid');

  // Sub-group cards
  const subGroupHtml = (movie._sub_groups || []).map(child => {
    const src = posterSrc(child);
    const imgHtml = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>\ud83c\udfa6</div>'">`
      : '<div class="no-img">\ud83c\udfa6</div>';
    const lbl = child._group_badge_label || child._group_title || child.title || '';
    return `
      <div class="eg-edition-card" onclick="openEditionGroupViewFromSuper(${child.id})">
        <div class="eg-edition-poster">
          ${imgHtml}
          <div class="eg-edition-fmt">${child.editions_count}\u00d7</div>
          ${lbl ? `<div class="eg-edition-type-label">${lbl}</div>` : ''}
        </div>
        <div class="eg-edition-info">${child._group_title || child.title || ''}</div>
      </div>`;
  });

  // Loose movie cards (direct members of super-group)
  // Pass skipGroupRedirect=true so the movie detail opens directly instead of
  // bouncing back to the collection/super-group via the fallback redirect block.
  const looseHtml = (movie._loose_movies || []).map(lm => {
    const src = posterSrc(lm);
    const imgHtml = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>\ud83c\udfa6</div>'">`
      : '<div class="no-img">\ud83c\udfa6</div>';
    const edType = lm.edition_type && lm.edition_type !== 'standard'
      ? _editionShortLabel(lm.edition_type, lm.custom_edition_label) : '';
    return `
      <div class="eg-edition-card" onclick="openMovieDetail(${lm.id}, true)">
        <div class="eg-edition-poster">
          ${imgHtml}
          <div class="eg-edition-fmt">${lm.format || '4K'}</div>
          ${edType ? `<div class="eg-edition-type-label">${edType}</div>` : ''}
        </div>
        <div class="eg-edition-info">${lm.title || ''} ${lm.year ? '<span style="color:var(--text-muted);font-size:0.75rem;">' + lm.year + '</span>' : ''}</div>
      </div>`;
  });

  grid.innerHTML = [...subGroupHtml, ...looseHtml].join('');
  _populateEgManageSection(movie);
  _populateEgHero(movie, 'Box Set');
  switchEgTab('members');
  // Load extended data from API (backdrop, description, collection)
  const pgId = movie._parent_group_id;
  if (pgId) {
    fetch(`${API}/edition-groups/${pgId}`)
      .then(r => r.ok ? r.json() : null)
      .then(g => {
        if (!g) return;
        _currentEgGroupData = g;
        _applyEgBackdrop(g.backdrop, movie);
        if (g.description) {
          const descEl = document.getElementById('egPanelDescription');
          if (descEl) descEl.textContent = g.description;
        }
        _populateEgManageSection(movie, g);
      });
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
  switchTabDirect('edition-group');
  if (movie._parent_group_id) _pushRoute(`/boxset/${movie._parent_group_id}`);
}

function openEditionGroupViewFromSuper(id) {
  _egViewStack.push({ type: 'super', movie: _currentSuperGroup });
  // Pass the child data directly — don't rely on allMovies lookup
  // (the super group card may share the same id as the first child)
  const child = (_currentSuperGroup._sub_groups || []).find(c => c.id === id);
  openEditionGroupView(id, child);
}

function openEditionGroupView(id, primaryOverride) {
  _currentEditionGroupPrimaryId = id;
  _currentSuperGroup = null;
  _currentCollection = null;
  _currentCollectionData = null;
  _currentEgGroupData = null;
  const primary = primaryOverride || allMovies.find(m => m.id === id);
  if (!primary) return;
  _currentEditionGroupId = primary.edition_group_id || null;
  // Ensure all editions are in allMovies so openMovieDetail can find them
  (primary.editions || []).forEach(e => {
    if (!allMovies.some(m => m.id === e.id))
      allMovies.push({ ...e, _isNested: true, _primaryId: id });
  });
  // Header
  document.getElementById('egPanelTitle').textContent = primary._group_title || primary.title || '';
  const fmtShort = { '4K UHD': '4K', 'Blu-ray': 'BD', 'DVD': 'DVD' };
  const editionTypes = [...new Set((primary.editions || []).map(e => {
    if (e.edition_type && e.edition_type !== 'standard')
      return e.edition_type === 'custom' ? (e.custom_edition_label || 'Custom') : _editionShortLabel(e.edition_type);
    return fmtShort[e.format] || (e.format || '');
  }).filter(Boolean))];
  document.getElementById('egPanelSubtitle').textContent =
    `${(primary.editions || []).length} ${t('js.editions', 'editions')}${editionTypes.length ? ' · ' + editionTypes.join(' · ') : ''}`;
  // Edition cards
  const grid = document.getElementById('egPanelGrid');
  grid.innerHTML = (primary.editions || []).map(e => {
    const src = posterSrc(e);
    const imgHtml = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
      : '<div class="no-img">🎬</div>';
    const typeLabel = e.edition_type && e.edition_type !== 'standard'
      ? (e.edition_type === 'custom' ? (e.custom_edition_label || 'Custom') : _editionShortLabel(e.edition_type))
      : '';
    const isPrimary = (e.id === id);
    return `
      <div class="eg-edition-card" onclick="openMovieDetail(${e.id}, true)">
        <div class="eg-edition-poster">
          ${imgHtml}
          <div class="eg-edition-fmt">${e.format || '4K'}</div>
          ${typeLabel ? `<div class="eg-edition-type-label">${typeLabel}</div>` : ''}
          ${isPrimary ? '<div class="eg-primary-marker">★</div>' : ''}
        </div>
        <div class="eg-edition-info">${e.year || ''}</div>
      </div>`;
  }).join('');
  _populateEgManageSection(primary);
  _populateEgHero(primary, 'Vault');
  switchEgTab('members');
  // Load extended data from API
  const groupId = primary.edition_group_id;
  if (groupId) {
    fetch(`${API}/edition-groups/${groupId}`)
      .then(r => r.ok ? r.json() : null)
      .then(g => {
        if (!g) return;
        _currentEgGroupData = g;
        _applyEgBackdrop(g.backdrop, primary);
        if (g.description) {
          const descEl = document.getElementById('egPanelDescription');
          if (descEl) descEl.textContent = g.description;
        }
        _populateEgManageSection(primary, g);
      });
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
  switchTabDirect('edition-group');
  _pushRoute(`/vault/${id}`);
}

function _populateEgManageSection(movieCard, groupData) {
  const isCollection = !!(_currentCollection && _currentCollection._collection_id);
  const gd = isCollection ? (_currentCollectionData || {}) : (groupData || _currentEgGroupData || {});

  const titleEl = document.getElementById('egGroupTitle');
  if (titleEl) titleEl.value = gd.title || (movieCard && movieCard._group_title) || '';
  const descEl = document.getElementById('egGroupDescription');
  if (descEl) descEl.value = gd.description || '';
  const elBl = document.getElementById('egBadgeLabel');
  if (elBl) elBl.value = (gd.badge_label || (movieCard && movieCard._group_badge_label)) || '';
  const typeEl = document.getElementById('egGroupType');
  const currentType = (_currentSuperGroup || gd.group_type === 'boxset') ? 'boxset' : 'vault';
  if (typeEl) typeEl.value = currentType;

  // Hide fields not applicable to collections
  const typeSection = document.getElementById('egGroupTypeSection');
  if (typeSection) typeSection.style.display = isCollection ? 'none' : '';
  const pgSection = document.getElementById('egParentGroupId');
  if (pgSection) pgSection.closest('.input-group').style.display = isCollection ? 'none' : '';
  const blSection = elBl;
  if (blSection) blSection.closest('.input-group').style.display = isCollection ? 'none' : '';
  const colSection = document.getElementById('egCollectionId');
  if (colSection) colSection.closest('.input-group').style.display = isCollection ? 'none' : '';

  // Determine container context for poster/backdrop uploads.
  // Use immediate card fields so the panel is available even before async data loads.
  let ctxType = null, ctxId = null;
  if (isCollection) {
    ctxType = 'collections';
    ctxId = _currentCollection._collection_id;
  } else if (_currentSuperGroup && _currentSuperGroup._parent_group_id) {
    // Box-set: edition_groups id stored in _parent_group_id on the aggregated card
    ctxType = 'edition-groups';
    ctxId = _currentSuperGroup._parent_group_id;
  } else if (gd && gd.id) {
    // Vault: edition group data loaded from API
    ctxType = 'edition-groups';
    ctxId = gd.id;
  } else if (movieCard && movieCard.edition_group_id) {
    // Vault: API data not yet loaded — use the primary movie's edition_group_id
    ctxType = 'edition-groups';
    ctxId = movieCard.edition_group_id;
  }
  _currentContainerImageCtx = (ctxType && ctxId) ? { type: ctxType, id: ctxId } : null;
  _refreshContainerImagePreviews(gd || {});

  const elPgId   = document.getElementById('egParentGroupId');
  const badge    = document.getElementById('egParentGroupBadge');
  const pgSearch = document.getElementById('egParentGroupSearch');
  if (pgSearch) pgSearch.value = '';

  if (!isCollection) {
    const existingParentId = gd.parent_group_id || (movieCard && movieCard._parent_group_id);
    if (elPgId) elPgId.value = existingParentId || '';
    if (existingParentId && badge) {
      fetch(`${API}/edition-groups/${existingParentId}`)
        .then(r => r.ok ? r.json() : null)
        .then(g => {
          if (!g) return;
          document.getElementById('egParentGroupName').textContent = g.title || `#${existingParentId}`;
          badge.style.display = 'flex';
        });
    } else if (badge) {
      badge.style.display = 'none';
    }

    // Collection link
    const colId = gd.collection_id || '';
    const elColId = document.getElementById('egCollectionId');
    const colBadge = document.getElementById('egCollectionBadge');
    const colSearch = document.getElementById('egCollectionSearch');
    if (elColId) elColId.value = colId || '';
    if (colSearch) colSearch.value = '';
    if (colId && colBadge) {
      fetch(`${API}/collections/${colId}`)
        .then(r => r.ok ? r.json() : null)
        .then(c => {
          if (!c) return;
          document.getElementById('egCollectionName').textContent = c.title || `#${colId}`;
          colBadge.style.display = 'flex';
        });
    } else if (colBadge) {
      colBadge.style.display = 'none';
    }
    updateEgTypeDependentFields();
  }
}

function updateEgTypeDependentFields() {
  const type = (document.getElementById('egGroupType') || {}).value || 'vault';
  const parentInput = document.getElementById('egParentGroupId');
  const parentGroup = parentInput && parentInput.closest('.input-group');
  const parentBadge = document.getElementById('egParentGroupBadge');
  const parentSearch = document.getElementById('egParentGroupSearch');
  if (!parentGroup) return;
  const isBoxSet = type === 'boxset';
  parentGroup.style.display = isBoxSet ? 'none' : '';
  if (isBoxSet) {
    if (parentInput) parentInput.value = '';
    if (parentSearch) parentSearch.value = '';
    if (parentBadge) parentBadge.style.display = 'none';
  }
}

function toggleEgManageSection() {
  switchEgTab('manage');
}

let _currentContainerImageCtx = null;

function _refreshContainerImagePreviews(gd) {
  const posterPrev = document.getElementById('egContainerPosterPreview');
  const bgPrev = document.getElementById('egContainerBackdropPreview');
  const clearBtn = document.getElementById('egContainerPosterClear');
  const wrap = document.getElementById('egContainerImagesGroup');
  if (!wrap) return;

  if (!_currentContainerImageCtx) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = '';

  // Apply translated labels at runtime so they're never raw keys
  const imLabel = document.getElementById('egContainerImagesLabel');
  if (imLabel) imLabel.textContent = t('edition.egContainerImages');
  const pLabel = document.getElementById('egContainerPosterLabel');
  if (pLabel) pLabel.textContent = t('edition.egContainerPoster');
  const bLabel = document.getElementById('egContainerBackdropLabel');
  if (bLabel) bLabel.textContent = t('edition.egContainerBackdrop');
  if (clearBtn) clearBtn.textContent = t('edition.egContainerClear');

  const pf = (gd && gd.poster_file) ? gd.poster_file : '';
  if (posterPrev) {
    if (pf) {
      posterPrev.style.backgroundImage = `url('${apiImageUrl(pf, 'poster')}')`;
      posterPrev.textContent = '';
    } else {
      posterPrev.style.backgroundImage = '';
      posterPrev.textContent = '+';
    }
  }
  if (clearBtn) clearBtn.style.display = pf ? '' : 'none';

  // Also update the large panel header poster when a container poster is set
  const egPosterEl = document.getElementById('egPoster');
  if (egPosterEl) {
    if (pf) {
      const url = apiImageUrl(pf, 'poster');
      egPosterEl.innerHTML = `<img src="${url}" alt="" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>📦</div>'">`;
    }
  }

  const bg = (gd && gd.backdrop) ? gd.backdrop : '';
  if (bgPrev) {
    if (bg) {
      bgPrev.style.backgroundImage = `url('${backdropSrc(bg)}')`;
      bgPrev.textContent = '';
    } else {
      bgPrev.style.backgroundImage = '';
      bgPrev.textContent = '+';
    }
  }
}

async function _reloadContainerCtxData() {
  if (!_currentContainerImageCtx) return null;
  const { type, id } = _currentContainerImageCtx;
  try {
    const r = await fetch(`${API}/${type === 'collections' ? 'collections' : 'edition-groups'}/${id}`);
    if (!r.ok) return null;
    const data = await r.json();
    if (type === 'collections') _currentCollectionData = data; else _currentEgGroupData = data;
    _refreshContainerImagePreviews(data);
    return data;
  } catch (_) { return null; }
}

async function uploadContainerPoster(inputEl) {
  if (!_currentContainerImageCtx || !inputEl || !inputEl.files || !inputEl.files[0]) return;
  const { type, id } = _currentContainerImageCtx;
  const fd = new FormData();
  fd.append('poster', inputEl.files[0]);
  try {
    const r = await fetch(`${API}/${type}/${id}/poster`, { method: 'POST', body: fd });
    inputEl.value = '';
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      alert(e.error || 'Upload mislukt');
      return;
    }
    // Directly update egPoster from the upload response so the panel header
    // refreshes immediately without waiting on _reloadContainerCtxData.
    const result = await r.json().catch(() => ({}));
    const pf = result.poster_file;
    if (pf) {
      const imgUrl = apiImageUrl(result.posterUrl || result.poster_url || pf, 'poster');
      const posterEl = document.getElementById('egPoster');
      if (posterEl) posterEl.innerHTML = `<img src="${imgUrl}" alt="" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>📦</div>'">`;
      const prev = document.getElementById('egContainerPosterPreview');
      if (prev) { prev.style.backgroundImage = `url('${imgUrl}')`; prev.textContent = ''; }
      const clearBtn = document.getElementById('egContainerPosterClear');
      if (clearBtn) clearBtn.style.display = '';
    }
    await _reloadContainerCtxData();
    if (typeof loadCollection === 'function') loadCollection();
  } catch (err) {
    alert('Upload mislukt: ' + err);
  }
}

async function clearContainerPoster() {
  if (!_currentContainerImageCtx) return;
  const { type, id } = _currentContainerImageCtx;
  try {
    const r = await fetch(`${API}/${type}/${id}/poster`, { method: 'DELETE' });
    if (!r.ok) return;
    await _reloadContainerCtxData();
    if (typeof loadCollection === 'function') loadCollection();
  } catch (_) {}
}

async function uploadContainerBackdrop(inputEl) {
  if (!_currentContainerImageCtx || !inputEl || !inputEl.files || !inputEl.files[0]) return;
  const { type, id } = _currentContainerImageCtx;
  const fd = new FormData();
  fd.append('backdrop', inputEl.files[0]);
  try {
    const r = await fetch(`${API}/${type}/${id}/backdrop`, { method: 'POST', body: fd });
    inputEl.value = '';
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      alert(e.error || 'Upload mislukt');
      return;
    }
    // Directly update the hero section from the upload response.
    const result = await r.json().catch(() => ({}));
    const bd = backdropSrc(result.backdropUrl || result.backdrop_url || result.backdrop);
    if (bd) {
      _applyEgBackdrop(bd, null);
      const prev = document.getElementById('egContainerBackdropPreview');
      if (prev) { prev.style.backgroundImage = `url('${bd}')`; prev.textContent = ''; }
    }
    await _reloadContainerCtxData();
    if (typeof loadCollection === 'function') loadCollection();
  } catch (err) {
    alert('Upload mislukt: ' + err);
  }
}

// ── Edition Group detail: hero, tabs, media ─────────────────────────────────

let _currentEgGroupData = null;

function _populateEgHero(movieCard, typeLabel) {
  // Type tag
  const tagsEl = document.getElementById('egPanelTags');
  if (tagsEl) tagsEl.innerHTML = `<span class="movie-tag">${typeLabel}</span>`;
  // Description
  const descEl = document.getElementById('egPanelDescription');
  if (descEl) descEl.textContent = '';
  // Poster — prefer the container's own uploaded poster if set, otherwise
  // fall back to the first member's poster.
  const posterEl = document.getElementById('egPoster');
  if (posterEl) {
    const containerPf = movieCard._container_poster_file;
    let src = containerPf ? posterSrc({ poster_file: containerPf }) : null;
    if (!src) {
      const firstMember = (movieCard.editions || movieCard._sub_groups || movieCard._loose_movies || [])[0];
      src = firstMember ? posterSrc(firstMember) : posterSrc(movieCard);
    }
    posterEl.innerHTML = src
      ? `<img src="${src}" alt="" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>📦</div>'">`
      : '<div class="no-img">📦</div>';
  }
  // Use the container's own backdrop if available, otherwise fall back to
  // a member's backdrop via the second argument.
  _applyEgBackdrop(movieCard.backdrop || null, movieCard);
}

function _applyEgBackdrop(backdropUrl, movieCard) {
  const heroWrap = document.getElementById('egHeroWrap');
  const heroImg = document.getElementById('egHeroImg');
  const bgBlur = document.getElementById('egDetailBg');
  backdropUrl = backdropSrc(backdropUrl);
  if (!backdropUrl) {
    // Fallback: try first member's backdrop
    const members = (movieCard && (movieCard.editions || movieCard._sub_groups || movieCard._loose_movies)) || [];
    for (const m of members) {
      const memberBackdrop = backdropSrc(m);
      if (memberBackdrop) { backdropUrl = memberBackdrop; break; }
    }
  }
  if (backdropUrl) {
    if (heroWrap) heroWrap.classList.remove('no-backdrop');
    // Preload image before showing — same pattern as openMovieDetail
    const img = new Image();
    img.onload = () => {
      if (heroImg) { heroImg.style.backgroundImage = `url('${backdropUrl}')`; heroImg.classList.add('loaded'); }
      if (bgBlur)  { bgBlur.style.backgroundImage  = `url('${backdropUrl}')`; bgBlur.classList.add('loaded'); }
    };
    img.onerror = () => {
      if (heroImg) heroImg.style.backgroundImage = '';
      if (heroWrap) heroWrap.classList.add('no-backdrop');
    };
    img.src = backdropUrl;
  } else {
    if (heroImg) { heroImg.style.backgroundImage = ''; heroImg.classList.remove('loaded'); }
    if (heroWrap) heroWrap.classList.add('no-backdrop');
    if (bgBlur)  { bgBlur.style.backgroundImage  = ''; bgBlur.classList.remove('loaded'); }
  }
}

function switchEgTab(name) {
  document.querySelectorAll('#panel-edition-group [data-eg-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-eg-tab') === name);
  });
  document.querySelectorAll('#panel-edition-group .modal-tab-content').forEach(el => {
    el.classList.remove('active');
  });
  const map = { members: 'egTabMembers', images: 'egTabImages', videos: 'egTabVideos', manage: 'egTabManage' };
  const target = document.getElementById(map[name]);
  if (target) target.classList.add('active');
  if (name === 'images' || name === 'videos') loadEgMedia();
}

function loadEgMedia() {
  const imgContainer = document.getElementById('egImagesContent');
  const vidContainer = document.getElementById('egVideosContent');
  const loading = `<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:0.88rem;">${t('general.loading', 'Laden...')}</div>`;
  const noMedia = `<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:0.88rem;">${t('modal.noMedia')}</div>`;
  if (imgContainer) imgContainer.innerHTML = loading;
  if (vidContainer) vidContainer.innerHTML = loading;

  // Determine if this is a collection or edition-group view
  if (_currentCollection && _currentCollection._collection_id) {
    _loadCollectionMedia(imgContainer, vidContainer, _currentCollection._collection_id);
    return;
  }

  const isSuperGroup = !!_currentSuperGroup;
  const groupId = isSuperGroup
    ? (_currentSuperGroup._parent_group_id)
    : _currentEditionGroupId;

  if (!groupId) {
    if (imgContainer) imgContainer.innerHTML = noMedia;
    if (vidContainer) vidContainer.innerHTML = noMedia;
    return;
  }

  fetch(`${API}/edition-groups/${groupId}`)
    .then(r => r.ok ? r.json() : null)
    .then(g => {
      if (!g) {
        if (imgContainer) imgContainer.innerHTML = noMedia;
        if (vidContainer) vidContainer.innerHTML = noMedia;
        return;
      }
      _currentEgGroupData = g;
      const allMembers = [...(g.members || []), ...(g.loose_movies || [])];
      if (imgContainer) _renderMediaGrid(imgContainer, allMembers, g.backdrop || '', 'eg', groupId);
      if (vidContainer) _renderVideosGrid(vidContainer, allMembers);
    });
}

function _loadCollectionMedia(imgContainer, vidContainer, colId) {
  const noMedia = `<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:0.88rem;">${t('modal.noMedia')}</div>`;
  fetch(`${API}/collections/${colId}`)
    .then(r => r.ok ? r.json() : null)
    .then(c => {
      if (!c) {
        if (imgContainer) imgContainer.innerHTML = noMedia;
        if (vidContainer) vidContainer.innerHTML = noMedia;
        return;
      }
      _currentCollectionData = c;
      const allMembers = [...(c.eg_movies || []), ...(c.loose_movies || [])];
      if (imgContainer) _renderMediaGrid(imgContainer, allMembers, c.backdrop || '', 'col', colId);
      if (vidContainer) _renderVideosGrid(vidContainer, allMembers);
    });
}

function _renderMediaGrid(container, allMembers, currentBackdrop, type, groupId) {
  let html = '';
  const activeBackdrop = backdropSrc(currentBackdrop);
  allMembers.forEach(m => {
    let backdrops = [];
    try { backdrops = m.backdrops ? JSON.parse(m.backdrops) : []; } catch(e) {}
    backdrops = backdrops.map(url => backdropSrc(url)).filter(Boolean);
    if (!backdrops.length) {
      const primaryBackdrop = backdropSrc(m);
      if (primaryBackdrop) backdrops = [primaryBackdrop];
    }
    if (!backdrops.length) return;

    html += `<div style="margin-bottom:20px;">
      <div style="font-size:0.78rem;font-weight:700;color:var(--text-muted);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;">${m.title || ''} ${m.year ? '(' + m.year + ')' : ''}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;">
        ${backdrops.map(url => {
          const isActive = url === activeBackdrop;
          return `<div style="position:relative;border-radius:8px;overflow:hidden;border:2px solid ${isActive ? 'var(--accent)' : 'transparent'};cursor:pointer;" onclick="setGroupBackdrop('${type}', ${groupId}, '${url.replace(/'/g, "\\'")}')">
            <img src="${url}" loading="lazy" style="width:100%;display:block;aspect-ratio:16/9;object-fit:cover;transition:transform .2s;" onmouseover="this.style.transform='scale(1.03)'" onmouseout="this.style.transform='scale(1)'">
            ${isActive ? '<div style="position:absolute;top:6px;right:6px;background:var(--accent);color:#0a0a0f;font-size:0.68rem;font-weight:700;padding:2px 8px;border-radius:4px;">Backdrop</div>' : ''}
          </div>`;
        }).join('')}
      </div>
    </div>`;
  });

  if (!html) {
    html = `<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:0.88rem;">${t('modal.noMedia')}</div>`;
  } else {
    html = `<p style="font-size:0.82rem;color:var(--text-muted);margin-bottom:16px;">${t('group.mediaHint', 'Klik op een afbeelding om deze als backdrop voor de groep in te stellen.')}</p>` + html;
  }
  container.innerHTML = html;
}

function _renderVideosGrid(container, allMembers) {
  let html = '';
  allMembers.forEach(m => {
    const items = [];
    const seenKeys = new Set();
    const addItem = (key, label, source) => {
      if (seenKeys.has(key)) return;
      if (!showAutoVideos && source === 'tmdb') return;
      seenKeys.add(key);
      items.push({ key, label });
    };
    const trailerUrl = m.trailer_url || '';
    const ytMatch = trailerUrl.match(/[?&]v=([^&]+)/) || trailerUrl.match(/youtu\.be\/([^?&]+)/);
    if (ytMatch) addItem(ytMatch[1], t('modal.trailer'), 'tmdb');
    let extraVideos = [];
    try { extraVideos = m.videos ? JSON.parse(m.videos) : []; } catch(e) {}
    for (const v of extraVideos) {
      const vm = v.url && (v.url.match(/[?&]v=([^&]+)/) || v.url.match(/youtu\.be\/([^?&]+)/));
      if (vm) addItem(vm[1], v.label || v.type || '', v.source || 'manual');
    }
    if (!items.length) return;
    html += `<div style="margin-bottom:20px;">
      <div style="font-size:0.78rem;font-weight:700;color:var(--text-muted);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;">${m.title || ''}${m.year ? ' (' + m.year + ')' : ''}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;">
        ${items.map(({key, label}) => `<div>
          <div style="font-size:0.75rem;font-weight:700;color:var(--text-muted);letter-spacing:0.07em;text-transform:uppercase;margin-bottom:6px;">${label}</div>
          ${_ytThumbHtml(key)}
        </div>`).join('')}
      </div>
    </div>`;
  });
  container.innerHTML = html || `<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:0.88rem;">${showAutoVideos ? t('modal.noVideosAuto') : t('modal.noVideosManual')}</div>`;
}

async function setGroupBackdrop(type, groupId, url) {
  const normalizedUrl = backdropSrc(url);
  const endpoint = type === 'col'
    ? `${API}/collections/${groupId}`
    : `${API}/edition-groups/${groupId}`;
  await fetch(endpoint, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ backdrop: normalizedUrl })
  });
  // Apply immediately
  const movieCard = _currentCollection || _currentSuperGroup || allMovies.find(m => m.id === _currentEditionGroupPrimaryId);
  _applyEgBackdrop(normalizedUrl, movieCard || {});
  if (type === 'col' && _currentCollectionData) _currentCollectionData.backdrop = normalizedUrl;
  if (type === 'eg' && _currentEgGroupData) _currentEgGroupData.backdrop = normalizedUrl;
  // Reload media tab to update active indicator
  loadEgMedia();
}

// ── Edition Group: Collection search/link ───────────────────────────────────

function searchEgCollection(query) {
  const dropdown = document.getElementById('egCollectionDropdown');
  if (!dropdown) return;
  if (!query || query.length < 1) { dropdown.style.display = 'none'; return; }
  fetch(`${API}/collections?q=${encodeURIComponent(query)}`)
    .then(r => r.json())
    .then(cols => {
      const items = cols.slice(0, 8).map(c => {
        const total = (c.eg_movie_count || 0) + (c.loose_movie_count || 0) + (c.boxset_loose_count || 0);
        return `<div style="padding:8px 12px; cursor:pointer; font-size:0.85rem;" onclick="selectEgCollection(${c.id}, '${(c.title||'').replace(/'/g,"\\'")}')">${c.title} <span style='color:var(--text-muted);font-size:0.75rem;'>(${total})</span></div>`;
      });
      items.push(`<div style="padding:8px 12px; cursor:pointer; font-size:0.85rem; border-top:1px solid var(--border); color:#2ecc71;" onclick="createAndSelectEgCollection('${query.replace(/'/g,"\\'")}')">➕ ${query}</div>`);
      dropdown.innerHTML = items.join('');
      dropdown.style.display = '';
    });
}

async function createAndSelectEgCollection(title) {
  const res = await fetch(`${API}/collections`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ title })
  });
  if (!res.ok) return;
  const c = await res.json();
  selectEgCollection(c.id, c.title);
}

function selectEgCollection(id, title) {
  document.getElementById('egCollectionId').value = id;
  document.getElementById('egCollectionSearch').value = '';
  document.getElementById('egCollectionDropdown').style.display = 'none';
  document.getElementById('egCollectionName').textContent = title;
  document.getElementById('egCollectionBadge').style.display = 'flex';
}

function unlinkEgCollection() {
  document.getElementById('egCollectionId').value = '';
  document.getElementById('egCollectionBadge').style.display = 'none';
}

function searchParentGroup(query) {
  const dropdown = document.getElementById('egParentGroupDropdown');
  if (!dropdown) return;
  if (!query || query.length < 1) { dropdown.style.display = 'none'; return; }
  fetch(`${API}/edition-groups?q=${encodeURIComponent(query)}`)
    .then(r => r.json())
    .then(groups => {
      const items = groups.slice(0, 8).map(g => {
        // Box sets have no direct members (member_count=0). The real films live in
        // child vaults (child_member_count) and loose movies (loose_movie_count).
        const total = (g.member_count || 0) + (g.child_member_count || 0) + (g.loose_movie_count || 0);
        return `<div style="padding:8px 12px; cursor:pointer; font-size:0.85rem;" onclick="selectParentGroup(${g.id}, '${(g.title||'').replace(/'/g,"\\'")}')">${g.title} <span style='color:var(--text-muted);font-size:0.75rem;'>(${total})</span></div>`;
      });
      // Always offer to create a new super-group with the typed name
      items.push(`<div style="padding:8px 12px; cursor:pointer; font-size:0.85rem; border-top:1px solid var(--border); color:var(--accent);" onclick="createAndSelectParentGroup('${query.replace(/'/g,"\\'")}')">➕ ${t('edition.egCreateGroup', query)}</div>`);
      dropdown.innerHTML = items.join('');
      dropdown.style.display = '';
    });
}

async function createAndSelectParentGroup(title) {
  const res = await fetch(`${API}/edition-groups`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ title, group_type: 'boxset' })
  });
  if (!res.ok) return;
  const g = await res.json();
  document.getElementById('egParentGroupSearch').value = '';
  document.getElementById('egParentGroupDropdown').style.display = 'none';
  selectParentGroup(g.id, g.title);
}

function selectParentGroup(id, title) {
  document.getElementById('egParentGroupId').value = id;
  document.getElementById('egParentGroupSearch').value = '';
  document.getElementById('egParentGroupDropdown').style.display = 'none';
  document.getElementById('egParentGroupName').textContent = title;
  document.getElementById('egParentGroupBadge').style.display = 'flex';
}

function unlinkParentGroup() {
  document.getElementById('egParentGroupId').value = '';
  document.getElementById('egParentGroupBadge').style.display = 'none';
}

async function saveEditionGroupMeta() {
  // Collection save
  if (_currentCollection && _currentCollection._collection_id) {
    const colId = _currentCollection._collection_id;
    const title = (document.getElementById('egGroupTitle') || {}).value || null;
    const description = (document.getElementById('egGroupDescription') || {}).value || null;
    await fetch(`${API}/collections/${colId}`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ title, description })
    });
    await loadCollection();
    closeEditionGroupView();
    return;
  }

  const primary = allMovies.find(m => m.id === _currentEditionGroupPrimaryId);
  // For super groups, the groupId is the parent_group_id stored on the super group card
  const groupId = _currentSuperGroup
    ? _currentSuperGroup._parent_group_id
    : (primary && primary.edition_group_id);
  if (!groupId) return;
  const title         = (document.getElementById('egGroupTitle') || {}).value || null;
  const description   = (document.getElementById('egGroupDescription') || {}).value || null;
  const badge_label   = (document.getElementById('egBadgeLabel') || {}).value || null;
  const group_type    = (document.getElementById('egGroupType') || {}).value === 'boxset' ? 'boxset' : 'vault';
  const parent_raw    = (document.getElementById('egParentGroupId') || {}).value;
  const parent_group_id = group_type === 'boxset' ? null : (parent_raw ? parseInt(parent_raw) : null);
  const col_raw       = (document.getElementById('egCollectionId') || {}).value;
  const collection_id = col_raw ? parseInt(col_raw) : null;
  await fetch(`${API}/edition-groups/${groupId}`, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ title, description, badge_label, parent_group_id, collection_id, group_type })
  });
  await loadCollection();
  closeEditionGroupView();
}

async function deleteEditionGroup() {
  // Collection delete
  if (_currentCollection && _currentCollection._collection_id) {
    const colId = _currentCollection._collection_id;
    const name = (document.getElementById('egGroupTitle') || {}).value || _currentCollection._group_title || '';
    if (!confirm(t('js.egDeleteConfirm', name))) return;
    await fetch(`${API}/collections/${colId}`, { method: 'DELETE' });
    await loadCollection();
    closeEditionGroupView();
    return;
  }

  const primary = allMovies.find(m => m.id === _currentEditionGroupPrimaryId);
  const groupId = _currentSuperGroup
    ? _currentSuperGroup._parent_group_id
    : (primary && primary.edition_group_id);
  if (!groupId) return;
  const name = (document.getElementById('egGroupTitle') || {}).value || (primary && primary._group_title) || '';
  if (!confirm(t('js.egDeleteConfirm', name))) return;
  await fetch(`${API}/edition-groups/${groupId}`, { method: 'DELETE' });
  await loadCollection();
  closeEditionGroupView();
}

function closeEditionGroupView() {
  if (_egViewStack.length > 0) {
    const prev = _egViewStack.pop();
    if (prev.type === 'collection') { openCollectionView(prev.movie); return; }
    if (prev.type === 'super') { openSuperGroupView(prev.movie); return; }
  }
  // Clean up backdrop so it doesn't bleed into other views
  const egBg = document.getElementById('egDetailBg');
  if (egBg) { egBg.classList.remove('loaded'); egBg.style.backgroundImage = ''; }
  const egHeroEl = document.getElementById('egHeroImg');
  if (egHeroEl) { egHeroEl.classList.remove('loaded'); egHeroEl.style.backgroundImage = ''; }
  _replaceRoute(_tabPath(_detailReturnTab));
  switchTabDirect(_detailReturnTab);
}

async function openMovieDetail(id, skipGroupRedirect) {
  // Remember which panel/tab we're coming from (for back navigation)
  // Only update if we're NOT already in movie-detail or person-detail
  const currentActive = document.querySelector('.panel.active');
  const currentPanelId = currentActive ? currentActive.id : '';
  if (currentPanelId !== 'panel-movie-detail' && currentPanelId !== 'panel-person-detail') {
    if (currentPanelId === 'panel-edition-group') {
      _detailReturnTab = 'edition-group';
    } else {
      // Determine return tab from active nav tab
      const activeTab = document.querySelector('.tab.active');
      _detailReturnTab = activeTab ? (activeTab.dataset.tab || 'collection') : 'collection';
    }
    // Capture the navigation list for swipe-between-movies
    if (currentPanelId === 'panel-search') {
      _detailNavList = getSearchMovies().map(m => m.id);
    } else if (currentPanelId !== 'panel-edition-group') {
      _detailNavList = getCurrentMovies().map(m => m.id);
    }
  }
  // When opening from person-detail, set person return panel for the new movie's back btn
  if (currentPanelId === 'panel-person-detail') {
    _detailReturnTab = 'person-detail';
  }

  currentMovieId = id;
  let movie = allMovies.find(m => m.id === id);
  if (!movie) {
    // Movie might not be in allMovies (e.g. loose movie inside a box set).
    // Fetch from API so watchlist / external links can still open it.
    try {
      const r = await fetch(`${API}/movies/${id}`);
      if (!r.ok) return;
      movie = await r.json();
      allMovies.push(movie);
    } catch { return; }
  }
  if (!movie._containers && !movie._is_collection && !movie._is_super_group && !movie._is_group) {
    try {
      const r = await fetch(`${API}/movies/${id}`);
      if (r.ok) {
        const fresh = await r.json();
        movie = { ...movie, ...fresh };
        const idx = allMovies.findIndex(m => m.id === id);
        if (idx >= 0) allMovies[idx] = { ...allMovies[idx], ...fresh };
        else allMovies.push(movie);
      }
    } catch {}
  }

  // Redirect grouped editions to the stack view when group_editions mode is active
  if (!skipGroupRedirect && !movie._isNested) {
    // Collection and box-set cards are always conceptual containers; open
    // their view regardless of editions_count or groupEditionsEnabled.
    if (movie._is_collection) { openCollectionView(movie); return; }
    if (movie._is_super_group) { openSuperGroupView(movie); return; }
    // Vault stacking is only relevant when group-editions mode is on and
    // there's more than one edition to stack.
    if (groupEditionsEnabled && (movie.editions_count || 0) > 1) {
      openEditionGroupView(id);
      return;
    }
    // Fallback: aggregated flags missing (e.g. deep-link before collection
    // loaded, or single-film container). Use raw container fields to find
    // the matching aggregated card or route directly.
    if (movie.collection_id) {
      const cc = allMovies.find(x => x._is_collection && x._collection_id === movie.collection_id);
      if (cc) { openCollectionView(cc); return; }
      _pushRoute(`/collection/${movie.collection_id}`);
    }
    if (movie.super_group_id) {
      const sg = allMovies.find(x => x._is_super_group && x._parent_group_id === movie.super_group_id);
      if (sg) { openSuperGroupView(sg); return; }
      _pushRoute(`/boxset/${movie.super_group_id}`);
    }
    if (movie.edition_group_id && groupEditionsEnabled) {
      openEditionGroupView(id);
      return;
    }
  }

  // Hide edit/refresh buttons if user doesn't own this movie (and isn't admin / auth disabled)
  const canEdit = canEditMovie(movie);
  const editBtn = document.getElementById('btnEditMovie');
  if (editBtn) editBtn.style.display = canEdit ? '' : 'none';
  const refreshBtn = document.getElementById('btnRefreshSingle');
  if (refreshBtn) refreshBtn.style.display = canEdit ? '' : 'none';

  // Determine local language code (non-English only)
  const _localLang = (currentLang && currentLang !== 'en') ? currentLang : null;
  const _localTitle = _localLang && showLocalTitle ? (movie[`title_${_localLang}`] || '') : '';
  const _localPlot  = _localLang ? (movie[`plot_${_localLang}`] || '') : '';

  document.getElementById('modalTitle').textContent = _localTitle || movie.title || '';
  document.getElementById('modalDirector').textContent = movie.director || '';
  const movieIdLabel = document.getElementById('modalMovieIdLabel');
  const movieIdStatus = document.getElementById('modalMovieIdStatus');
  if (movieIdLabel) movieIdLabel.textContent = `ID: ${movie.id}`;
  if (movieIdStatus) movieIdStatus.textContent = '';

  const tags = document.getElementById('modalTags');
  tags.innerHTML = '';
  if (movie.format)          tags.innerHTML += `<span class="tag format">${movie.format}</span>`;
  if (movie.year)            tags.innerHTML += `<span class="tag">${movie.year}</span>`;
  // Preferred content rating: from per-country JSON, fallback to manual audience_rating
  let _preferredRating = '';
  if (movie.content_ratings) {
    try { const _cr = JSON.parse(movie.content_ratings); _preferredRating = _cr[preferredRatingCountry] || ''; } catch(e) {}
  }
  if (!_preferredRating) _preferredRating = movie.audience_rating || '';
  if (_preferredRating) tags.innerHTML += `<span class="tag">${_preferredRating}</span>`;
  if (movie.hdr)             tags.innerHTML += `<span class="tag" style="color:#7cf">${movie.hdr}</span>`;

  // Movie detail always shows the film's OWN poster, never the container's.
  const src = posterSrc({ ...movie, _container_poster_file: null });
  const poster = document.getElementById('modalPoster');
  poster.innerHTML = src
    ? `<img src="${src}" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
    : '<div class="no-img">🎬</div>';

  const bg   = document.getElementById('movieDetailBg');
  const hero = document.getElementById('detailHeroImg');
  const heroWrap = document.querySelector('.detail-hero-wrap');
  const backdropUrl = backdropSrc(movie);
  const blurSrc = backdropUrl || src || '';  // fallback: poster as blurred bg

  // Page background blur — use backdrop if available, else poster as ambience
  if (bg) {
    bg.classList.remove('loaded');
    if (blurSrc) {
      bg.style.backgroundImage = `url('${blurSrc}')`;
      const bgImg = new Image();
      bgImg.onload = () => bg.classList.add('loaded');
      bgImg.src = blurSrc;
    } else {
      bg.style.backgroundImage = '';
    }
  }

  // Hero banner — show backdrop image; collapse hero if no backdrop
  if (heroWrap) heroWrap.classList.toggle('no-backdrop', !backdropUrl);
  if (hero) {
    hero.classList.remove('loaded');
    if (backdropUrl) {
      hero.style.backgroundImage = `url('${backdropUrl}')`;
      const heroImg = new Image();
      heroImg.onload = () => hero.classList.add('loaded');
      heroImg.onerror = () => {
        // Backdrop failed to load — collapse hero
        hero.style.backgroundImage = '';
        if (heroWrap) heroWrap.classList.add('no-backdrop');
      };
      heroImg.src = backdropUrl;
    } else {
      hero.style.backgroundImage = '';
    }
  }

  const d = document.getElementById('modalDetails');
  const row = (label, val, full=false) =>
    val ? `<div class="detail-item${full?' full':''}"><label>${label}</label><span>${val}</span></div>` : '';
  const link = (label, href, text) =>
    href ? `<div class="detail-item"><label>${label}</label><span><a href="${href}" target="_blank" style="color:var(--accent)">${text} ↗</a></span></div>` : '';
  const containerSummary = _renderMovieContainerSummary(movie);

  d.innerHTML = [
    row(t('detail.containers', 'Containers'), containerSummary, true),
    row(t('d.genre'),          movie.genre, true),
    row(t('d.origTitle'),movie.original_title),
    row(t('d.country'),           movie.country),
    row(t('d.language'),           movie.language),
    row(t('d.releaseDate'),   movie.release_date),
    row(t('d.runtime'),      movie.runtime ? movie.runtime + t('d.min') : ''),
    row(t('d.aspectRatio'), movie.screen_ratios),
    row(t('d.packaging'),     movie.packaging),
    row(t('d.region'),          movie.regions),
    movie.rating ? `<div class="detail-item"><label>${t('d.imdbRating')}</label><span class="rating-stars">★ ${movie.rating}</span></div>` : '',
    row(t('d.contentRating'),     _preferredRating ? `<span style="display:inline-flex;align-items:center;gap:5px;">${_flagImg(preferredRatingCountry.toLowerCase())} ${_preferredRating}</span>` : (movie.audience_rating || '')),
    row(t('d.distributor'),   movie.distributor),
    row(t('d.studio'),         movie.studios),
    row(t('d.boxSet'),        movie.box_set, true),
    row(t('d.edition'),         movie.edition),
    row(t('d.editionYear'),    movie.edition_release_year),
    row(t('d.editionDate'),   movie.edition_release_date),
    row(t('d.audio'),          movie.audio_tracks, true),
    row(t('d.subtitles'),    movie.subtitles, true),
    row(t('d.extras'),       movie.extras, true),
    row(t('d.purchasePrice'),   movie.purchase_price),
    row(t('d.purchaseDate'),   movie.purchase_date),
    row(t('d.location'),        movie.location),
    (movie.barcode && !movie.barcode.startsWith('IMPORT-')) ? `<div class="detail-item"><label>${t('d.barcode')}</label><span style="font-family:'DM Mono',monospace;font-size:0.8rem">${movie.barcode}</span></div>` : '',
    link('IMDb',          movie.imdb_url || (movie.imdb_id ? `https://www.imdb.com/title/${movie.imdb_id}` : ''), movie.imdb_id || 'Open'),
    row(t('d.added'),     movie.added_at ? movie.added_at.slice(0,10) : ''),
    row(t('d.notes'),       movie.notes, true),
  ].join('');

  const _displayPlot = _localPlot || movie.plot || '';
  document.getElementById('modalPlot').textContent = _displayPlot;
  document.getElementById('modalPlotGroup').style.display = _displayPlot ? 'block' : 'none';

  // English original block: show when displaying a localised title or plot
  const _showingLocalTitle = !!(_localTitle && _localTitle !== movie.title);
  const _showingLocalPlot  = !!(_localPlot  && _localPlot  !== movie.plot);
  const enBlock  = document.getElementById('modalEnOriginalBlock');
  const enTitle  = document.getElementById('modalEnTitle');
  const enPlot   = document.getElementById('modalEnPlot');
  if (enBlock) {
    const _showEn = _showingLocalTitle || _showingLocalPlot;
    enBlock.style.display = _showEn ? 'block' : 'none';
    if (enTitle) enTitle.textContent = _showingLocalTitle ? (movie.title || '') : '';
    if (enPlot)  enPlot.textContent  = _showingLocalPlot  ? (movie.plot  || '') : '';
    // hide title line if not different
    if (enTitle) enTitle.style.display = _showingLocalTitle ? '' : 'none';
    if (enPlot)  enPlot.style.display  = _showingLocalPlot  ? '' : 'none';
  }

  // Debug: populate multilingual title + plot block (visibility controlled by applyDebugVisibility)
  const _debugI18nContent = document.getElementById('modalDebugI18nContent');
  if (_debugI18nContent) {
    // Parse per-country content ratings
    let _ratings = {};
    try { _ratings = movie.content_ratings ? JSON.parse(movie.content_ratings) : {}; } catch(e) {}

    const _i18nLangs = [
      { code: 'nl', name: 'Nederlands', country: 'NL' },
      { code: 'fr', name: 'Français',   country: 'FR' },
      { code: 'de', name: 'Deutsch',    country: 'DE' },
      { code: 'es', name: 'Español',    country: 'ES' },
      { code: 'pt', name: 'Português',  country: 'PT' },
      { code: 'it', name: 'Italiano',   country: 'IT' },
    ];
    const _ratingBadge = c => c
      ? `<span style="display:inline-block;font-size:0.68rem;font-weight:700;padding:1px 6px;border:1px solid rgba(255,165,0,0.5);border-radius:4px;color:#f90;margin-left:8px;vertical-align:middle;">${c}</span>`
      : '';

    // Langs with title or plot
    const _i18nItems = _i18nLangs.filter(l => movie[`title_${l.code}`] || movie[`plot_${l.code}`]);

    // Countries shown at the bottom: EN variants + langs with rating but no title/plot
    const _enCountries = ['US', 'GB', 'CA'];
    const _bottomCountries = [
      ..._i18nLangs.filter(l => _ratings[l.country] && !_i18nItems.find(x => x.country === l.country))
                   .map(l => l.country),
      ..._enCountries.filter(c => _ratings[c]),
    ];

    const hasContent = _i18nItems.length || _bottomCountries.length;
    if (hasContent) {
      let html = _i18nItems.map((l, i) => {
        const divider = (i < _i18nItems.length - 1 || _bottomCountries.length)
          ? 'margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid rgba(255,165,0,0.18);'
          : '';
        return `<div style="${divider}">
          <div style="font-weight:600;font-size:0.92rem;margin-bottom:4px;display:flex;align-items:center;gap:6px;">
            ${_flagImg(l.code)} <span>${movie[`title_${l.code}`] || '<em style="opacity:.5">—</em>'}</span>
            ${_ratingBadge(_ratings[l.country])}
          </div>
          ${movie[`plot_${l.code}`] ? `<div style="font-size:0.83rem;color:var(--text-muted);line-height:1.55;">${movie[`plot_${l.code}`]}</div>` : ''}
        </div>`;
      }).join('');

      if (_bottomCountries.length) {
        html += `<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;${_i18nItems.length ? 'margin-top:4px;' : ''}">
          ${_bottomCountries.map(c => `<span style="display:inline-flex;align-items:center;gap:5px;font-size:0.82rem;color:var(--text-muted);">${_flagImg(c.toLowerCase())} ${c} ${_ratingBadge(_ratings[c])}</span>`).join('')}
        </div>`;
      }
      _debugI18nContent.innerHTML = html;
    } else {
      _debugI18nContent.innerHTML = '<span style="font-size:0.83rem;color:var(--text-muted);">Geen vertalingen beschikbaar voor deze film.</span>';
    }
  }

  // Digital play links (Plex / Jellyfin) — only for users with digital.view permission
  const _dlSection = document.getElementById('modalDigitalLinksSection');
  const _dlBtns    = document.getElementById('modalDigitalLinksBtns');
  if (_dlSection && _dlBtns) {
    const _entry = (userHasDigital && compareData)
      ? (compareData.physical_and_digital || []).find(e => e.movie && e.movie.id === id)
      : null;
    const _matches = _entry
      ? _digitalMatches(_entry).filter(dm => dm.webUrl || dm.appUrl || dm.nativeUrl)
      : [];
    if (_matches.length) {
      _dlBtns.innerHTML = _matches.map(_renderDigitalPlayBtn).join('');
      _dlSection.style.display = '';
    } else {
      _dlSection.style.display = 'none';
    }
  }

  // Reset to info tab and scroll to top
  switchDetailTab('info');
  window.scrollTo({ top: 0, behavior: 'smooth' });
  document.getElementById('castContent').style.display = 'none';
  document.getElementById('castLoading').style.display = 'block';
  document.getElementById('castContent').innerHTML = '';
  _castLoaded = false;
  _mediaLoaded = false;

  // Update watchlist / watched state from allMovies cache
  const cachedMovie = allMovies.find(m => m.id === id);
  _modalMovieOnWatchlist = cachedMovie ? !!cachedMovie.on_watchlist : false;
  _modalMovieLastWatched  = cachedMovie ? (cachedMovie.last_watched || null) : null;
  _updateWatchlistBtn();
  _updateWatchedBtn();

  // Update nav arrows visibility and position counter
  _updateDetailNavUI();

  // Navigate to the movie-detail panel
  switchTabDirect('movie-detail');
  _pushRoute(`/movie/${id}`);
}

function closeMovieDetail() {
  // If in edit mode with unsaved changes, ask to save
  const editMode = document.getElementById('modalEditMode');
  if (editMode && editMode.style.display !== 'none' && _isEditDirty()) {
    if (confirm(t('js.unsavedChanges'))) { saveEdit(); return; }
  }
  _editDirty = false;
  // Clear backdrop/blur so they don't flash when re-opening
  const bg = document.getElementById('movieDetailBg');
  if (bg) { bg.classList.remove('loaded'); bg.style.backgroundImage = ''; }
  const hero = document.getElementById('detailHeroImg');
  if (hero) { hero.classList.remove('loaded'); hero.style.backgroundImage = ''; }
  // Reset to view mode when closing
  document.getElementById('modalViewMode').style.display = '';
  document.getElementById('modalEditMode').style.display = 'none';
  // Restore URL and navigate back
  if (_detailReturnTab === 'edition-group') {
    // Return to the edition group stack panel (its content is still rendered)
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    const egPanel = document.getElementById('panel-edition-group');
    if (egPanel) egPanel.classList.add('active');
    // Force the members (Inhoud) tab so the films list is visible again
    if (typeof switchEgTab === 'function') switchEgTab('members');
    _replaceRoute('/');
  } else {
    _replaceRoute(_tabPath(_detailReturnTab));
    switchTabDirect(_detailReturnTab);
  }
}

// ── Digital play link button renderer ───────────────────────────────────────
const _PLEX_SVG_PATH = 'M3.987 8.409c-.96 0-1.587.28-2.12.933v-.72H0v8.88s.038.018.127.037c.138.03.821.187 1.331-.249.441-.377.542-.814.542-1.318v-1.283c.533.573 1.147.813 2 .813 1.84 0 3.253-1.493 3.253-3.48 0-2.12-1.36-3.613-3.266-3.613Zm16.748 5.595.406.591c.391.614.894.906 1.492.908.621-.012 1.064-.562 1.226-.755 0 0-.307-.27-.686-.72-.517-.614-1.214-1.755-1.24-1.803l-1.198 1.779Zm-3.205-1.955c0-2.08-1.52-3.64-3.52-3.64s-3.467 1.587-3.467 3.573a3.48 3.48 0 0 0 3.507 3.52c1.413 0 2.626-.84 3.253-2.293h-2.04l-.093.093c-.427.4-.72.533-1.227.533-.787 0-1.373-.506-1.453-1.266h4.986c.04-.214.054-.307.054-.52Zm-7.671-.219c0 .769.11 1.701.868 2.722l.056.069c-.306.526-.742.88-1.248.88-.399 0-.814-.211-1.138-.579a2.177 2.177 0 0 1-.538-1.441V6.409H9.86l-.001 5.421Zm9.283 3.46h-2.39l2.247-3.332-2.247-3.335h2.39l2.248 3.335-2.248 3.332Zm1.593-1.286Zm-17.162-.342c-.933 0-1.68-.773-1.68-1.72s.76-1.666 1.68-1.666c.92 0 1.68.733 1.68 1.68 0 .946-.733 1.706-1.68 1.706Zm18.361-1.974L24 8.622h-2.391l-.87 1.293 1.195 1.773Zm-9.404-.466c.16-.706.72-1.133 1.493-1.133.773 0 1.373.467 1.507 1.133h-3Z';
const _JELLYFIN_SVG_PATH = 'M12 .002C8.826.002-1.398 18.537.16 21.666c1.56 3.129 22.14 3.094 23.682 0C25.384 18.573 15.177 0 12 0zm7.76 18.949c-1.008 2.028-14.493 2.05-15.514 0C3.224 16.9 9.92 4.755 12.003 4.755c2.081 0 8.77 12.166 7.759 14.196zM12 9.198c-1.054 0-4.446 6.15-3.93 7.189.518 1.04 7.348 1.027 7.86 0 .511-1.027-2.874-7.19-3.93-7.19z';

/**
 * Try to open the native app first (via URI scheme); fall back to the web URL
 * after 1.5 s if the page is still visible (app not installed / not available).
 * Called from onclick on the play buttons.
 */
function _openDigital(appUrl, webUrl, event) {
  event.preventDefault();
  event.stopPropagation();
  if (!appUrl) {
    // No native URI — open web URL directly in new tab
    window.open(webUrl, '_blank', 'noopener,noreferrer');
    return;
  }
  // Attempt to launch the native app
  window.location.href = appUrl;
  // If the app is not installed the browser stays visible; open web fallback
  setTimeout(() => {
    if (!document.hidden && webUrl) {
      window.open(webUrl, '_blank', 'noopener,noreferrer');
    }
  }, 1500);
}

function _renderDigitalPlayBtn(match) {
  const normalized = _normalizeDigitalMatch(match);
  const isPlex     = normalized.sourceType === 'plex';
  const isJellyfin = normalized.sourceType === 'jellyfin';
  const bg   = isPlex ? '#E5A00D' : (isJellyfin ? '#00A4DC' : '#555');
  const fg   = isPlex ? '#1a1a1a' : '#fff';
  const path = isPlex ? _PLEX_SVG_PATH : (isJellyfin ? _JELLYFIN_SVG_PATH : '');
  const icon = path
    ? `<svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor" style="flex-shrink:0" xmlns="http://www.w3.org/2000/svg"><path d="${path}"/></svg>`
    : '▶';
  const safeName = (normalized.sourceName || (isPlex ? 'Plex' : 'Jellyfin')).replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  const webUrl   = (normalized.webUrl || '').replace(/"/g,'&quot;');
  const appUrl   = (normalized.appUrl || normalized.nativeUrl || '').replace(/"/g,'&quot;');
  const label = (t('digital.playOn') || 'Play on {0}').replace('{0}', safeName);
  // href = web URL (right-click "open in new tab" still works);
  // onclick tries native app first, falls back to web after 1.5 s
  return `<a href="${webUrl || appUrl}" target="_blank" rel="noopener noreferrer"
    onclick="_openDigital('${appUrl}','${webUrl}',event)"
    style="display:inline-flex;align-items:center;gap:7px;padding:7px 14px;border-radius:7px;
           background:${bg};color:${fg};text-decoration:none;font-size:0.84rem;
           font-weight:600;line-height:1;transition:opacity 0.15s;"
    onmouseover="this.style.opacity='.8'" onmouseout="this.style.opacity='1'"
    title="${label}">${icon}<span>${label}</span></a>`;
}

function _normalizeDigitalMatch(match) {
  const digital = match && match.digital ? match.digital : {};
  return {
    sourceType: match?.sourceType || match?.source_type || digital.sourceType || digital.source_type || '',
    sourceName: match?.sourceName || match?.source_name || digital.sourceName || digital.source_name || '',
    webUrl: match?.webUrl || match?.web_url || match?.digitalWebUrl || match?.digital_web_url || digital.webUrl || digital.web_url || '',
    appUrl: match?.appUrl || match?.app_url || match?.digitalAppUrl || match?.digital_app_url || digital.appUrl || digital.app_url || '',
    nativeUrl: match?.nativeUrl || match?.native_url || match?.digitalNativeUrl || match?.digital_native_url || digital.nativeUrl || digital.native_url || '',
  };
}

function _digitalMatches(entry) {
  return (entry?.digitalMatches || entry?.digital_matches || []).map(_normalizeDigitalMatch);
}

// Alias kept for any legacy inline calls
function closeModalDirect() { closeMovieDetail(); }
function closeModal(e) {}

let _castLoaded = false;
let _mediaLoaded = false;

function switchDetailTab(tab) {
  document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.modal-tab-content').forEach(c => c.classList.remove('active'));
  const tabBtn = document.querySelector(`.modal-tab[data-detail-tab="${tab}"]`);
  if (tabBtn) tabBtn.classList.add('active');
  const tabMap = { info: 'detailTabInfo', cast: 'detailTabCast', images: 'detailTabImages', videos: 'detailTabVideos' };
  const tabContent = document.getElementById(tabMap[tab] || 'detailTabInfo');
  if (tabContent) tabContent.classList.add('active');
  if (tab === 'cast' && !_castLoaded) {
    _castLoaded = true;
    loadMovieCast(currentMovieId);
  }
  if ((tab === 'images' || tab === 'videos') && !_mediaLoaded) {
    _mediaLoaded = true;
    loadMovieMedia();
  }
}

function _playYouTube(key) {
  const el = document.getElementById(`ytThumb_${key}`);
  if (!el) return;
  el.innerHTML = `<iframe style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;" src="https://www.youtube-nocookie.com/embed/${key}?autoplay=1" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen title="Trailer"></iframe>`;
  el.style.removeProperty('cursor');
  el.onclick = null;
}

function _parseMediaList(raw) {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw.map(v => String(v || '').trim()).filter(Boolean);
  const text = String(raw || '').trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed) ? parsed.map(v => String(v || '').trim()).filter(Boolean) : [String(parsed || '').trim()].filter(Boolean);
  } catch(e) {
    return [text];
  }
}

function _moviePosterChoices(movie) {
  const rawValues = [
    ..._parseMediaList(movie.posters),
    movie.poster_file,
    movie.posterFile,
    movie.poster_url,
    movie.posterUrl,
    movie.cover_url,
    movie.coverUrl,
    movie.poster,
  ];
  const choices = [];
  const seen = new Set();
  for (const raw of rawValues) {
    const value = String(raw || '').trim();
    if (!value) continue;
    const src = /^https?:\/\//i.test(value) || value.startsWith('/api/')
      ? apiImageUrl(value, 'poster')
      : apiImageUrl(value.split(/[\\/]/).pop(), 'poster');
    if (!src || seen.has(src)) continue;
    seen.add(src);
    choices.push({ value, src });
  }
  return choices;
}

function loadMovieMedia() {
  const movie = allMovies.find(m => m.id === currentMovieId) || {};
  const posters = _moviePosterChoices(movie);

  // Backdrops
  let backdrops = [];
  try { backdrops = movie.backdrops ? JSON.parse(movie.backdrops) : []; } catch(e) {}
  backdrops = backdrops.map(url => backdropSrc(url)).filter(Boolean);
  if (!backdrops.length) {
    const primaryBackdrop = backdropSrc(movie);
    if (primaryBackdrop) backdrops = [primaryBackdrop];
  }

  // Trailer
  const trailerUrl = movie.trailer_url || '';
  const ytMatch = trailerUrl.match(/[?&]v=([^&]+)/) || trailerUrl.match(/youtu\.be\/([^?&]+)/);

  // Extra videos
  let extraVideos = [];
  try { extraVideos = movie.videos ? JSON.parse(movie.videos) : []; } catch(e) {}

  // --- Images tab ---
  const imgContainer = document.getElementById('mediaImagesContent');
  if (imgContainer) {
    const sections = [];
    if (posters.length > 0) {
      const currentPoster = posterSrc({ ...movie, _container_poster_file: null }) || '';
      const currentPosterFile = String(movie.poster_file || movie.posterFile || '').split(/[\\/]/).pop();
      sections.push(`<section style="margin-bottom:22px;">
        <div style="font-size:0.75rem;font-weight:700;color:var(--text-muted);letter-spacing:0.07em;text-transform:uppercase;border-bottom:1px solid var(--border);padding-bottom:6px;margin-bottom:10px;">${t('modal.posters')}</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(124px,1fr));gap:10px;">
          ${posters.map(item => {
            const choiceFile = String(item.value || '').split(/[\\/]/).pop();
            const isActive = item.src === currentPoster || (currentPosterFile && choiceFile === currentPosterFile);
            return `<button type="button" title="${escHtml(t('modal.usePoster'))}" aria-pressed="${isActive ? 'true' : 'false'}" style="appearance:none;background:transparent;padding:0;position:relative;border-radius:8px;overflow:hidden;border:2px solid ${isActive ? 'var(--accent)' : 'var(--border)'};cursor:pointer;box-shadow:${isActive ? '0 0 0 2px rgba(64,224,208,.22)' : 'none'};" onclick="setMoviePoster(${movie.id}, ${JSON.stringify(item.value).replace(/</g, '\\u003c')})">
              <img src="${item.src}" loading="lazy" style="width:100%;display:block;aspect-ratio:2/3;object-fit:cover;transition:transform .2s;" onmouseover="this.style.transform='scale(1.03)'" onmouseout="this.style.transform='scale(1)'">
              ${isActive ? `<div style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.08),rgba(0,0,0,.34));pointer-events:none;"></div><div style="position:absolute;top:6px;right:6px;background:var(--accent);color:#0a0a0f;font-size:0.68rem;font-weight:800;padding:3px 8px;border-radius:4px;">${t('modal.selected')}</div><div style="position:absolute;left:7px;bottom:7px;width:24px;height:24px;border-radius:50%;background:var(--accent);color:#0a0a0f;font-size:0.9rem;font-weight:900;display:flex;align-items:center;justify-content:center;">✓</div>` : ''}
            </button>`;
          }).join('')}
        </div>
      </section>`);
    }
    if (backdrops.length > 0) {
      const currentBackdrop = backdropSrc(movie);
      sections.push(`<section>
        <div style="font-size:0.75rem;font-weight:700;color:var(--text-muted);letter-spacing:0.07em;text-transform:uppercase;border-bottom:1px solid var(--border);padding-bottom:6px;margin-bottom:10px;">${t('modal.backdrops')}</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px;">
          ${backdrops.map(url => {
          const isActive = url === currentBackdrop;
          return `<button type="button" aria-pressed="${isActive ? 'true' : 'false'}" style="appearance:none;background:transparent;padding:0;position:relative;border-radius:8px;overflow:hidden;border:2px solid ${isActive ? 'var(--accent)' : 'transparent'};cursor:pointer;box-shadow:${isActive ? '0 0 0 2px rgba(64,224,208,.22)' : 'none'};" onclick="setMovieBackdrop(${movie.id}, '${url.replace(/'/g, "\\'")}')">
            <img src="${url}" loading="lazy" style="width:100%;display:block;aspect-ratio:16/9;object-fit:cover;transition:transform .2s;" onmouseover="this.style.transform='scale(1.03)'" onmouseout="this.style.transform='scale(1)'">
            ${isActive ? `<div style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.08),rgba(0,0,0,.34));pointer-events:none;"></div><div style="position:absolute;top:6px;right:6px;background:var(--accent);color:#0a0a0f;font-size:0.68rem;font-weight:800;padding:3px 8px;border-radius:4px;">${t('modal.selected')}</div><div style="position:absolute;left:7px;bottom:7px;width:24px;height:24px;border-radius:50%;background:var(--accent);color:#0a0a0f;font-size:0.9rem;font-weight:900;display:flex;align-items:center;justify-content:center;">✓</div>` : ''}
          </button>`;
          }).join('')}
        </div>
      </section>`);
    }
    if (sections.length) {
      imgContainer.innerHTML = sections.join('');
    } else {
      imgContainer.innerHTML = `<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:0.88rem;">${t('modal.noMedia')}</div>`;
    }
  }

  // --- Videos tab ---
  const vidContainer = document.getElementById('mediaVideosContent');
  if (vidContainer) {
    // Build flat list, optionally filtering auto-fetched
    const seenKeys = new Set();
    const allVidItems = [];
    const addItem = (key, label, videoType, source) => {
      if (seenKeys.has(key)) return;
      if (!showAutoVideos && source === 'tmdb') return;
      seenKeys.add(key);
      allVidItems.push({ key, label, type: videoType, source });
    };
    if (ytMatch) addItem(ytMatch[1], '', 'Trailer', 'tmdb');
    for (const v of extraVideos) {
      const vm = v.url && (v.url.match(/[?&]v=([^&]+)/) || v.url.match(/youtu\.be\/([^?&]+)/));
      if (vm) addItem(vm[1], v.label || '', v.type || '', v.source || 'manual');
    }
    if (allVidItems.length > 0) {
      // Group by type in canonical order
      const typeOrder = [..._VIDEO_TYPES, ''];
      const grouped = Object.fromEntries(typeOrder.map(k => [k, []]));
      for (const item of allVidItems) {
        const cat = _VIDEO_TYPES.includes(item.type) ? item.type : '';
        grouped[cat].push(item);
      }
      let html = '';
      for (const typeKey of typeOrder) {
        const items = grouped[typeKey];
        if (!items.length) continue;
        const heading = typeKey || t('modal.videoTypeOther');
        html += `<div style="margin-bottom:24px;">
          <div style="font-size:0.75rem;font-weight:700;color:var(--text-muted);letter-spacing:0.07em;text-transform:uppercase;border-bottom:1px solid var(--border);padding-bottom:6px;margin-bottom:10px;">${heading}</div>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;">
            ${items.map(item => `<div>
              ${item.label ? `<div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:5px;">${escHtml(item.label)}</div>` : ''}
              ${_ytThumbHtml(item.key)}
            </div>`).join('')}
          </div>
        </div>`;
      }
      vidContainer.innerHTML = html;
    } else {
      vidContainer.innerHTML = `<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:0.88rem;">${showAutoVideos ? t('modal.noVideosAuto') : t('modal.noVideosManual')}</div>`;
    }
  }
}

function _ytThumbHtml(key) {
  return `<div id="ytThumb_${key}" onclick="_playYouTube('${key}')" style="position:relative;width:100%;aspect-ratio:16/9;border-radius:10px;overflow:hidden;background:#111;cursor:pointer;">
    <img src="https://img.youtube.com/vi/${key}/maxresdefault.jpg" onerror="this.onerror=null;this.src='https://img.youtube.com/vi/${key}/hqdefault.jpg'" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;" alt="">
    <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:56px;height:40px;background:rgba(180,0,0,0.88);border-radius:8px;display:flex;align-items:center;justify-content:center;">
      <span style="display:block;width:0;height:0;border-style:solid;border-width:12px 0 12px 22px;border-color:transparent transparent transparent #fff;margin-left:4px;"></span>
    </div>
  </div>`;
}

async function setMoviePoster(movieId, value) {
  const response = await fetch(`${API}/movies/${movieId}/poster-choice`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    showStatus('editStatus', data.error || t('js.backendError', response.status), 'error');
    return;
  }
  if (data.movie) {
    const idx = allMovies.findIndex(m => m.id === movieId);
    if (idx >= 0) allMovies[idx] = data.movie;
  }
  const movie = allMovies.find(m => m.id === movieId) || data.movie || {};
  const src = posterSrc({ ...movie, _container_poster_file: null });
  const poster = document.getElementById('modalPoster');
  if (poster) {
    poster.innerHTML = src
      ? `<img src="${src}" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
      : '<div class="no-img">🎬</div>';
  }
  loadMovieMedia();
  filterMovies();
}

async function setMovieBackdrop(movieId, url) {
  const normalizedUrl = backdropSrc(url);
  await fetch(`${API}/movies/${movieId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ backdrop: normalizedUrl })
  });
  // Update local cache and apply immediately
  const movie = allMovies.find(m => m.id === movieId);
  if (movie) movie.backdrop = normalizedUrl;
  // Update hero/bg on the detail page
  const hero = document.getElementById('detailHeroImg');
  const bg = document.getElementById('movieDetailBg');
  const heroWrap = document.querySelector('.detail-hero-wrap');
  if (heroWrap) heroWrap.classList.remove('no-backdrop');
  if (hero) {
    hero.classList.remove('loaded');
    hero.style.backgroundImage = `url('${normalizedUrl}')`;
    const img = new Image();
    img.onload = () => hero.classList.add('loaded');
    img.src = normalizedUrl;
  }
  if (bg) {
    bg.classList.remove('loaded');
    bg.style.backgroundImage = `url('${normalizedUrl}')`;
    const img = new Image();
    img.onload = () => bg.classList.add('loaded');
    img.src = normalizedUrl;
  }
  // Reload media tab to update active indicator
  loadMovieMedia();
}

async function loadMovieCast(movieId) {
  const loading = document.getElementById('castLoading');
  const content = document.getElementById('castContent');
  loading.style.display = 'block';
  content.style.display = 'none';
  try {
    const r = await fetch(`${API}/movies/${movieId}/cast`);
    const cast = await r.json();
    if (!cast.length) {
      content.innerHTML = `<div style="text-align:center; padding:30px; color:var(--text-muted);">${t('modal.noCast')}</div>`;
      loading.style.display = 'none';
      content.style.display = 'block';
      return;
    }
    const actors = cast.filter(c => c.role === 'actor');
    const crew = cast.filter(c => c.role === 'crew');
    // Group crew by job
    const crewByJob = {};
    crew.forEach(c => {
      const job = c.job || 'Other';
      if (!crewByJob[job]) crewByJob[job] = [];
      crewByJob[job].push(c);
    });

    let html = '';
    if (actors.length) {
      html += `<div class="crew-section-title">${t('d.actors')} (${actors.length})</div>`;
      html += '<div class="cast-grid">';
      actors.forEach(a => {
        const photoSrc = profileSrc(a);
        const photo = photoSrc
          ? `<img class="cast-photo" src="${photoSrc}" onerror="this.outerHTML='<div class=\\'cast-photo-placeholder\\'>👤</div>'">`
          : '<div class="cast-photo-placeholder">👤</div>';
        const charName = a.character ? `<div class="cast-role">${t('person.as')} ${escHtml(a.character)}</div>` : '';
        html += `<div class="cast-card" onclick="openPersonDetail(${a.person_id})">
          ${photo}
          <div class="cast-name">${escHtml(a.name)}</div>
          ${charName}
        </div>`;
      });
      html += '</div>';
    }
    for (const [job, members] of Object.entries(crewByJob)) {
      html += `<div class="crew-section-title">${escHtml(job)} (${members.length})</div>`;
      html += '<div class="cast-grid">';
      members.forEach(c => {
        const photoSrc = profileSrc(c);
        const photo = photoSrc
          ? `<img class="cast-photo" src="${photoSrc}" onerror="this.outerHTML='<div class=\\'cast-photo-placeholder\\'>👤</div>'">`
          : '<div class="cast-photo-placeholder">👤</div>';
        html += `<div class="cast-card" onclick="openPersonDetail(${c.person_id})">
          ${photo}
          <div class="cast-name">${escHtml(c.name)}</div>
        </div>`;
      });
      html += '</div>';
    }
    content.innerHTML = html;
    loading.style.display = 'none';
    content.style.display = 'block';
  } catch(e) {
    content.innerHTML = `<div style="text-align:center; padding:30px; color:var(--danger);">${t('js.error', e.message)}</div>`;
    loading.style.display = 'none';
    content.style.display = 'block';
  }
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _renderMovieContainerSummary(movie) {
  const containers = movie._containers || {};
  const vaults = containers.vaults || [];
  const boxSets = containers.box_sets || [];
  const directCollections = containers.collections_direct || [];
  const viaCollections = containers.collections_via_containers || [];
  const chip = (label, value, color, onclick) => `
    <button type="button" onclick="${onclick}" style="display:inline-flex;align-items:center;gap:5px;margin:0 6px 6px 0;padding:4px 8px;border:1px solid ${color}66;border-radius:6px;background:${color}18;color:${color};font-size:0.78rem;cursor:pointer;">
      <strong style="font-size:0.7rem;">${label}</strong> ${escHtml(value)}
    </button>`;
  const parts = [];
  vaults.forEach(v => parts.push(chip('Vault', v.title || `#${v.id}`, '#e8c547', `openVaultById(${v.id}, ${movie.id})`)));
  boxSets.forEach(bs => parts.push(chip('Box Set', bs.title || `#${bs.id}`, '#7c6af7', `openBoxSetById(${bs.id})`)));
  directCollections.forEach(c => parts.push(chip('Collection', c.title || `#${c.id}`, '#2ecc71', `openCollectionById(${c.id})`)));
  viaCollections.forEach(c => {
    const via = c.via_type === 'box_set' ? 'via Box Set' : 'via Vault';
    parts.push(chip(via, `${c.title || '#' + c.id} (${c.via_title || ''})`, '#78d69d', `openCollectionById(${c.id})`));
  });
  if (!parts.length) return '';
  return `<div style="display:flex;flex-wrap:wrap;gap:0;">${parts.join('')}</div>`;
}

function _renderEditContainerSummary(movie) {
  const wrap = document.getElementById('editContainerSummaryWrap');
  const target = document.getElementById('editContainerSummary');
  if (!wrap || !target) return;
  const containers = movie._containers || {};
  const vaults = containers.vaults || [];
  const boxSets = containers.box_sets || [];
  const directCollections = containers.collections_direct || [];
  const viaCollections = containers.collections_via_containers || [];
  const lines = [];
  if (vaults.length) lines.push(`<strong style="color:var(--accent);">Vault</strong>: ${vaults.map(v => escHtml(v.title || '#' + v.id)).join(', ')}`);
  if (boxSets.length) lines.push(`<strong style="color:var(--accent2);">Box Set</strong>: ${boxSets.map(bs => escHtml(bs.title || '#' + bs.id)).join(', ')}`);
  if (directCollections.length) lines.push(`<strong style="color:#2ecc71;">Directe Collection</strong>: ${directCollections.map(c => escHtml(c.title || '#' + c.id)).join(', ')}`);
  if (viaCollections.length) {
    lines.push(`<strong style="color:#78d69d;">Collection via container</strong>: ${viaCollections.map(c => `${escHtml(c.title || '#' + c.id)} via ${c.via_type === 'box_set' ? 'Box Set' : 'Vault'} ${escHtml(c.via_title || '')}`).join(', ')}`);
  }
  target.innerHTML = lines.length
    ? lines.map(line => `<div>${line}</div>`).join('')
    : t('edit.containerSummaryEmpty', 'Deze film is nog niet gekoppeld aan een Vault, Box Set of Collection.');
  wrap.style.display = '';
}

async function openPersonDetail(personId) {
  // Remember which panel we came from for the back button
  const currentActive = document.querySelector('.panel.active');
  const currentPanelId = currentActive ? currentActive.id : '';
  _personReturnPanel = (currentPanelId === 'panel-movie-detail') ? 'movie-detail' : 'collection';

  currentPersonId = personId;
  // Navigate to person-detail panel
  switchTabDirect('person-detail');
  _pushRoute(`/person/${personId}`);
  document.getElementById('personName').textContent = '';
  document.getElementById('personMeta').innerHTML = '';
  const personIdLabel = document.getElementById('personIdLabel');
  const personIdStatus = document.getElementById('personIdStatus');
  if (personIdLabel) personIdLabel.textContent = `ID: ${personId}`;
  if (personIdStatus) personIdStatus.textContent = '';
  document.getElementById('personBio').textContent = '';
  document.getElementById('personPhoto').innerHTML = '<div class="person-photo-placeholder-large">👤</div>';
  document.getElementById('personFilmography').innerHTML = '<span class="spinner"></span>';
  // Scroll to top of panel
  window.scrollTo({ top: 0, behavior: 'smooth' });
  try {
    const r = await fetch(`${API}/people/${personId}`);
    const p = await r.json();
    document.getElementById('personName').textContent = p.name || '';
    // Photo
    const personPhotoSrc = profileSrc(p);
    if (personPhotoSrc) {
      document.getElementById('personPhoto').innerHTML =
        `<img class="person-photo-large" src="${personPhotoSrc}" onerror="this.outerHTML='<div class=\\'person-photo-placeholder-large\\'>👤</div>'">`;
    }
    // Meta info
    let meta = [];
    if (p.known_for) meta.push(`${t('person.knownFor')}: ${p.known_for}`);
    if (p.birthday) meta.push(`${t('person.born')}: ${p.birthday}`);
    if (p.deathday) meta.push(`${t('person.died')}: ${p.deathday}`);
    if (p.place_of_birth) meta.push(`${t('person.birthPlace')}: ${p.place_of_birth}`);
    document.getElementById('personMeta').innerHTML = meta.join(' · ');
    // Biography
    document.getElementById('personBio').textContent = p.biography || t('person.noBio');

    // Debug: populate multilingual biography block (visibility controlled by applyDebugVisibility)
    const _personDebugContent = document.getElementById('personDebugI18nContent');
    if (_personDebugContent) {
      const _bioLangs = [
        { code: 'nl', name: 'Nederlands' },
        { code: 'fr', name: 'Français' },
        { code: 'de', name: 'Deutsch' },
        { code: 'es', name: 'Español' },
        { code: 'pt', name: 'Português' },
        { code: 'it', name: 'Italiano' },
      ];
      const _bioItems = _bioLangs.filter(l => p[`biography_${l.code}`]);
      if (_bioItems.length) {
        _personDebugContent.innerHTML = _bioItems.map((l, i) => `
          <div style="margin-bottom:${i < _bioItems.length - 1 ? '14px' : '0'}; padding-bottom:${i < _bioItems.length - 1 ? '14px' : '0'}; ${i < _bioItems.length - 1 ? 'border-bottom:1px solid rgba(255,165,0,0.18);' : ''}">
            <div style="font-weight:600; font-size:0.82rem; margin-bottom:6px; display:flex; align-items:center; gap:6px;">${_flagImg(l.code)} <span>${l.name}</span></div>
            <div style="font-size:0.83rem; color:var(--text-muted); line-height:1.55;">${p[`biography_${l.code}`]}</div>
          </div>`).join('');
      } else {
        _personDebugContent.innerHTML = '<span style="font-size:0.83rem; color:var(--text-muted);">Geen vertalingen beschikbaar voor deze persoon.</span>';
      }
    }
    // Build collection movies HTML
    const movies = p.movies || [];
    let collectionHtml = '';
    if (movies.length) {
      movies.forEach(m => {
        const src = posterSrc(m) || '';
        const posterImg = src
          ? `<img class="person-film-poster" src="${src}" onerror="this.style.display='none'">`
          : `<div class="person-film-poster" style="display:flex;align-items:center;justify-content:center;font-size:1.5rem;color:var(--text-muted)">🎬</div>`;
        const role = m.character ? `<div class="person-film-year">${t('person.as')} ${escHtml(m.character)}</div>`
                   : m.job ? `<div class="person-film-year">${escHtml(m.job)}</div>` : '';
        collectionHtml += `<div class="person-film-card" onclick="_detailReturnTab='person-detail';openMovieDetail(${m.id})">`;
        collectionHtml += `<div style="position:relative">${posterImg}<div class="person-film-format-badge">${escHtml(m.format || '')}</div></div>`;
        collectionHtml += `<div class="person-film-title">${escHtml(m.title)}</div><div class="person-film-year">${m.year || ''}</div>${role}</div>`;
      });
    } else {
      collectionHtml = `<div style="color:var(--text-muted);font-size:0.85rem;">${t('js.noMoviesFound')}</div>`;
    }

    if (detailedActorDetails) {
      // Show pill tabs + sort dropdown
      const titleWrap = document.getElementById('personFilmographyTitleWrap');
      const sortLabels = [
        ['newest', t('person.sortNewest') || 'Nieuwste eerst'],
        ['oldest', t('person.sortOldest') || 'Oudste eerst'],
        ['az', 'A–Z'],
        ['rating', t('person.sortRating') || 'TMDb beoordeling'],
      ];
      const sortOptions = sortLabels.map(([s, lbl]) =>
        `<option value="${s}">${escHtml(lbl)}</option>`
      ).join('');
      titleWrap.innerHTML =
        `<div class="person-film-pill-bar">` +
          `<div class="person-film-pill-tabs">` +
            `<button class="person-film-pill active" id="personTabBtnCollection" onclick="_switchPersonTab('collection')">${t('person.tabCollection') || 'In collectie'} <span class="person-film-pill-count" id="personTabCountCollection">${movies.length}</span></button>` +
            `<button class="person-film-pill" id="personTabBtnDigital" onclick="_switchPersonTab('digital')">${t('person.tabDigital') || 'Digitale bibliotheek'} <span class="person-film-pill-count" id="personTabCountDigital">…</span></button>` +
            `<button class="person-film-pill" id="personTabBtnFilmography" onclick="_switchPersonTab('filmography')">${t('person.tabFilmography') || 'Filmografie'} <span class="person-film-pill-count" id="personTabCountFilmography">…</span></button>` +
          `</div>` +
          `<select class="person-film-sort-select" id="personFilmSort" onchange="_sortFilmography(this.value)">${sortOptions}</select>` +
        `</div>`;
      const filmEl = document.getElementById('personFilmography');
      filmEl.style.display = 'block';
      filmEl.innerHTML =
        `<div class="modal-tab-content active" id="personTabCollection">` +
          `<div class="person-filmography" id="personCollectionGrid"></div>` +
        `</div>` +
        `<div class="modal-tab-content" id="personTabDigital">` +
          `<div class="person-filmography" id="personDigitalGrid"><span class="spinner"></span></div>` +
        `</div>` +
        `<div class="modal-tab-content" id="personTabFilmography">` +
          `<div class="person-filmography" id="personFilmographyGrid"><span class="spinner"></span></div>` +
        `</div>`;
      _personFilmographySort = 'newest';
      _personCollectionMovies = movies;
      _personRatingMap = null;
      _renderCollectionGrid(movies, 'newest', null);
      _loadPersonFilmography(personId);
    } else {
      // Simple mode: just show collection
      const titleWrap = document.getElementById('personFilmographyTitleWrap');
      if (titleWrap) titleWrap.innerHTML = `<div class="crew-section-title" id="personFilmographyTitle">${t('person.inCollection') || 'In jouw collectie'}</div>`;
      const filmEl = document.getElementById('personFilmography');
      filmEl.style.display = '';
      filmEl.innerHTML = collectionHtml;
    }
  } catch(e) {
    document.getElementById('personFilmography').innerHTML =
      `<div style="color:var(--danger)">${t('js.error', e.message)}</div>`;
  }
}

// ── Person filmography helpers ──────────────────────────────────────────────
let _personFilmographyData = null;
let _personFilmographySort = 'newest';
let _personCollectionMovies = null;
let _personRatingMap = null;

function _switchPersonTab(tab) {
  ['collection', 'digital', 'filmography'].forEach(id => {
    const cap = id[0].toUpperCase() + id.slice(1);
    document.getElementById('personTab' + cap)?.classList.toggle('active', id === tab);
    document.getElementById('personTabBtn' + cap)?.classList.toggle('active', id === tab);
  });
  if (tab === 'filmography' && _personFilmographyData) _renderFilmographyGrid(_personFilmographyData, _personFilmographySort);
  if (tab === 'digital' && _personFilmographyData) _renderDigitalGrid(_personFilmographyData, _personFilmographySort);
}

async function _loadPersonFilmography(personId) {
  _personFilmographyData = null;
  const langMap = {nl:'nl-NL', fr:'fr-FR', de:'de-DE', es:'es-ES', pt:'pt-PT', it:'it-IT'};
  const lang = langMap[currentLang] || 'en-US';
  try {
    const r = await fetch(`${API}/people/${personId}/filmography?language=${lang}`);
    const data = await r.json();
    _personFilmographyData = data;
    // Build rating lookup map: tmdb_id → vote_average
    const ratingMap = {};
    [...(data.cast || []), ...(data.crew || [])].forEach(m => {
      if (m.tmdb_id && m.vote_average > 0) ratingMap[String(m.tmdb_id)] = m.vote_average;
    });
    _personRatingMap = ratingMap;
    // Update pill counts
    const digitalCount = (data.cast || []).filter(_filmographyInDigital).length;
    const elD = document.getElementById('personTabCountDigital');
    const elF = document.getElementById('personTabCountFilmography');
    if (elD) elD.textContent = digitalCount;
    if (elF) elF.textContent = (data.cast || []).length;
    // Re-render collection grid with ratings now available
    if (_personCollectionMovies) _renderCollectionGrid(_personCollectionMovies, _personFilmographySort, ratingMap);
    // Pre-render all grids so tab switches are instant
    const filmGrid = document.getElementById('personFilmographyGrid');
    if (filmGrid) _renderFilmographyGrid(data, _personFilmographySort);
    const digGrid = document.getElementById('personDigitalGrid');
    if (digGrid) _renderDigitalGrid(data, _personFilmographySort);
  } catch(e) {
    const grid = document.getElementById('personFilmographyGrid');
    if (grid) grid.innerHTML = `<div style="color:var(--danger)">${t('js.error', e.message)}</div>`;
    ['personTabCountDigital','personTabCountFilmography'].forEach(id => { const el = document.getElementById(id); if (el) el.textContent = '!'; });
  }
}

function _sortFilmography(sort) {
  _personFilmographySort = sort;
  const sel = document.getElementById('personFilmSort');
  if (sel && sel.value !== sort) sel.value = sort;
  const active = ['collection','digital','filmography'].find(id =>
    document.getElementById('personTab' + id[0].toUpperCase() + id.slice(1))?.classList.contains('active')
  );
  if (active === 'collection' && _personCollectionMovies) _renderCollectionGrid(_personCollectionMovies, sort, _personRatingMap);
  else if (active === 'digital' && _personFilmographyData) _renderDigitalGrid(_personFilmographyData, sort);
  else if (active === 'filmography' && _personFilmographyData) _renderFilmographyGrid(_personFilmographyData, sort);
}

function _filmographyLocalMovieId(m) {
  return m.movieId || m.movie_id || m.collectionId || m.collection_id || (m.movie && (m.movie.movieId || m.movie.movie_id || m.movie.collectionId || m.movie.collection_id));
}

function _filmographyInCollection(m) {
  return !!(m.inCollection || m.in_collection || (m.movie && (m.movie.inCollection || m.movie.in_collection)));
}

function _filmographyInDigital(m) {
  return !!(m.inDigital || m.in_digital || (m.movie && (m.movie.inDigital || m.movie.in_digital)));
}

function _filmographyFormat(m) {
  return m.collectionFormat || m.collection_format || (m.movie && (m.movie.collectionFormat || m.movie.collection_format)) || '';
}

function _filmographyDigitalSource(m) {
  return m.digitalSource || m.digital_source || (m.digital && (m.digital.sourceType || m.digital.source_type)) || (m.movie && (m.movie.digitalSource || m.movie.digital_source)) || '';
}

function _renderCollectionGrid(movies, sort, ratingMap) {
  const grid = document.getElementById('personCollectionGrid');
  if (!grid) return;
  let sorted = [...movies];
  if (sort === 'newest') sorted.sort((a, b) => (b.year || '0').localeCompare(a.year || '0'));
  else if (sort === 'oldest') sorted.sort((a, b) => (a.year || '9999').localeCompare(b.year || '9999'));
  else if (sort === 'az') sorted.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
  else if (sort === 'rating') sorted.sort((a, b) => {
    const ra = ratingMap ? (ratingMap[String(a.tmdb_id)] || 0) : 0;
    const rb = ratingMap ? (ratingMap[String(b.tmdb_id)] || 0) : 0;
    return rb - ra;
  });
  if (!sorted.length) {
    grid.innerHTML = `<div style="color:var(--text-muted);font-size:0.85rem;">${t('js.noMoviesFound')}</div>`;
    return;
  }
  let html = '';
  sorted.forEach(m => {
    const src = posterSrc(m) || '';
    const posterImg = src
      ? `<img class="person-film-poster" src="${src}" onerror="this.style.display='none'">`
      : `<div class="person-film-poster" style="display:flex;align-items:center;justify-content:center;font-size:1.5rem;color:var(--text-muted)">🎬</div>`;
    const formatBadge = m.format ? `<div class="person-film-format-badge">${escHtml(m.format)}</div>` : '';
    const voteAvg = ratingMap ? (ratingMap[String(m.tmdb_id)] || 0) : 0;
    const rating = voteAvg > 0 ? `<div class="person-film-rating">⭐ ${voteAvg.toFixed(1)}</div>` : '';
    const role = m.character ? `<div class="person-film-year">${t('person.as')} ${escHtml(m.character)}</div>`
               : m.job ? `<div class="person-film-year">${escHtml(m.job)}</div>` : '';
    html += `<div class="person-film-card" onclick="_detailReturnTab='person-detail';openMovieDetail(${m.id})">`;
    html += `<div style="position:relative">${posterImg}${formatBadge}</div>`;
    html += `<div class="person-film-title">${escHtml(m.title)}</div>`;
    html += `<div class="person-film-year">${m.year || ''}</div>${rating}${role}`;
    html += `</div>`;
  });
  grid.innerHTML = html;
}

function _renderDigitalGrid(data, sort) {
  const grid = document.getElementById('personDigitalGrid');
  if (!grid) return;
  let movies = (data.cast || []).filter(_filmographyInDigital);
  if (sort === 'newest') movies.sort((a, b) => (b.year || '0').localeCompare(a.year || '0'));
  else if (sort === 'oldest') movies.sort((a, b) => (a.year || '9999').localeCompare(b.year || '9999'));
  else if (sort === 'az') movies.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
  else if (sort === 'rating') movies.sort((a, b) => (b.vote_average || 0) - (a.vote_average || 0));
  if (!movies.length) {
    grid.innerHTML = `<div style="color:var(--text-muted);font-size:0.85rem;">Geen digitale films gevonden voor deze persoon.</div>`;
    return;
  }
  let html = '';
  movies.forEach(m => {
    const localMovieId = _filmographyLocalMovieId(m);
    const hasCollectionLink = _filmographyInCollection(m) && localMovieId;
    const onclick = hasCollectionLink ? `onclick="_detailReturnTab='person-detail';openMovieDetail(${localMovieId})"` : '';
    const cursor = hasCollectionLink ? '' : 'cursor:default;';
    const src = posterSrc(m);
    const poster = src
      ? `<img class="person-film-poster" src="${escHtml(src)}" onerror="this.style.display='none'" loading="lazy">`
      : `<div class="person-film-poster" style="display:flex;align-items:center;justify-content:center;font-size:1.5rem;color:var(--text-muted)">\uD83C\uDFAC</div>`;
    const logo = _filmographyDigitalSource(m) === 'plex'
      ? '<svg viewBox="0 0 24 24" width="12" height="12" fill="#E5A00D"><path d="M3.987 8.409c-.96 0-1.587.28-2.12.933v-.72H0v8.88s.038.018.127.037c.138.03.821.187 1.331-.249.441-.377.542-.814.542-1.318v-1.283c.533.573 1.147.813 2 .813 1.84 0 3.253-1.493 3.253-3.48 0-2.12-1.36-3.613-3.266-3.613Zm16.748 5.595.406.591c.391.614.894.906 1.492.908.621-.012 1.064-.562 1.226-.755 0 0-.307-.27-.686-.72-.517-.614-1.214-1.755-1.24-1.803l-1.198 1.779Zm-3.205-1.955c0-2.08-1.52-3.64-3.52-3.64s-3.467 1.587-3.467 3.573a3.48 3.48 0 0 0 3.507 3.52c1.413 0 2.626-.84 3.253-2.293h-2.04l-.093.093c-.427.4-.72.533-1.227.533-.787 0-1.373-.506-1.453-1.266h4.986c.04-.214.054-.307.054-.52Zm-7.671-.219c0 .769.11 1.701.868 2.722l.056.069c-.306.526-.742.88-1.248.88-.399 0-.814-.211-1.138-.579a2.177 2.177 0 0 1-.538-1.441V6.409H9.86l-.001 5.421Zm9.283 3.46h-2.39l2.247-3.332-2.247-3.335h2.39l2.248 3.335-2.248 3.332Zm1.593-1.286Zm-17.162-.342c-.933 0-1.68-.773-1.68-1.72s.76-1.666 1.68-1.666c.92 0 1.68.733 1.68 1.68 0 .946-.733 1.706-1.68 1.706Zm18.361-1.974L24 8.622h-2.391l-.87 1.293 1.195 1.773Zm-9.404-.466c.16-.706.72-1.133 1.493-1.133.773 0 1.373.467 1.507 1.133h-3Z"/></svg>'
      : '<svg viewBox="0 0 24 24" width="12" height="12" fill="#00A4DC"><path d="M12 .002C8.826.002-1.398 18.537.16 21.666c1.56 3.129 22.14 3.094 23.682 0C25.384 18.573 15.177 0 12 0zm7.76 18.949c-1.008 2.028-14.493 2.05-15.514 0C3.224 16.9 9.92 4.755 12.003 4.755c2.081 0 8.77 12.166 7.759 14.196zM12 9.198c-1.054 0-4.446 6.15-3.93 7.189.518 1.04 7.348 1.027 7.86 0 .511-1.027-2.874-7.19-3.93-7.19z"/></svg>';
    const sourceBadge = `<div class="person-film-format-badge" style="background:rgba(20,20,28,0.82);display:flex;align-items:center;justify-content:center;padding:3px 5px;">${logo}</div>`;
    const formatBadge = _filmographyInCollection(m) && _filmographyFormat(m) ? `<div class="person-film-format-badge">${escHtml(_filmographyFormat(m))}</div>` : '';
    const rating = m.vote_average > 0 ? `<div class="person-film-rating">\u2B50 ${m.vote_average.toFixed(1)}</div>` : '';
    const role = m.character ? `<div class="person-film-year">${t('person.as')} ${escHtml(m.character)}</div>` : '';
    html += `<div class="person-film-card" style="${cursor}" ${onclick}>`;
    html += `<div style="position:relative">${poster}${formatBadge || sourceBadge}</div>`;
    html += `<div class="person-film-title">${escHtml(m.title)}</div>`;
    html += `<div class="person-film-year">${m.year || ''}</div>${rating}${role}`;
    html += `</div>`;
  });
  grid.innerHTML = html;
}

function _renderDigitalGrid(data, sort) {
  const grid = document.getElementById('personDigitalGrid');
  if (!grid) return;
  let movies = (data.cast || []).filter(_filmographyInDigital);
  if (sort === 'newest') movies.sort((a, b) => (b.year || '0').localeCompare(a.year || '0'));
  else if (sort === 'oldest') movies.sort((a, b) => (a.year || '9999').localeCompare(b.year || '9999'));
  else if (sort === 'az') movies.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
  else if (sort === 'rating') movies.sort((a, b) => (b.vote_average || 0) - (a.vote_average || 0));
  if (!movies.length) {
    grid.innerHTML = `<div style="color:var(--text-muted);font-size:0.85rem;">Geen digitale films gevonden voor deze persoon.</div>`;
    return;
  }
  let html = '';
  movies.forEach(m => {
    const localMovieId = _filmographyLocalMovieId(m);
    const hasCollectionLink = _filmographyInCollection(m) && localMovieId;
    const onclick = hasCollectionLink ? `onclick="_detailReturnTab='person-detail';openMovieDetail(${localMovieId})"` : '';
    const cursor = hasCollectionLink ? '' : 'cursor:default;';
    const src = posterSrc(m);
    const poster = src
      ? `<img class="person-film-poster" src="${escHtml(src)}" onerror="this.style.display='none'" loading="lazy">`
      : `<div class="person-film-poster" style="display:flex;align-items:center;justify-content:center;font-size:1.5rem;color:var(--text-muted)">🎬</div>`;
    const digitalSource = _filmographyDigitalSource(m);
    const logo = digitalSource === 'plex'
      ? '<svg viewBox="0 0 24 24" width="12" height="12" fill="#E5A00D"><path d="M3.987 8.409c-.96 0-1.587.28-2.12.933v-.72H0v8.88s.038.018.127.037c.138.03.821.187 1.331-.249.441-.377.542-.814.542-1.318v-1.283c.533.573 1.147.813 2 .813 1.84 0 3.253-1.493 3.253-3.48 0-2.12-1.36-3.613-3.266-3.613Zm16.748 5.595.406.591c.391.614.894.906 1.492.908.621-.012 1.064-.562 1.226-.755 0 0-.307-.27-.686-.72-.517-.614-1.214-1.755-1.24-1.803l-1.198 1.779Zm-3.205-1.955c0-2.08-1.52-3.64-3.52-3.64s-3.467 1.587-3.467 3.573a3.48 3.48 0 0 0 3.507 3.52c1.413 0 2.626-.84 3.253-2.293h-2.04l-.093.093c-.427.4-.72.533-1.227.533-.787 0-1.373-.506-1.453-1.266h4.986c.04-.214.054-.307.054-.52Zm-7.671-.219c0 .769.11 1.701.868 2.722l.056.069c-.306.526-.742.88-1.248.88-.399 0-.814-.211-1.138-.579a2.177 2.177 0 0 1-.538-1.441V6.409H9.86l-.001 5.421Zm9.283 3.46h-2.39l2.247-3.332-2.247-3.335h2.39l2.248 3.335-2.248 3.332Zm1.593-1.286Zm-17.162-.342c-.933 0-1.68-.773-1.68-1.72s.76-1.666 1.68-1.666c.92 0 1.68.733 1.68 1.68 0 .946-.733 1.706-1.68 1.706Zm18.361-1.974L24 8.622h-2.391l-.87 1.293 1.195 1.773Zm-9.404-.466c.16-.706.72-1.133 1.493-1.133.773 0 1.373.467 1.507 1.133h-3Z"/></svg>'
      : '<svg viewBox="0 0 24 24" width="12" height="12" fill="#00A4DC"><path d="M12 .002C8.826.002-1.398 18.537.16 21.666c1.56 3.129 22.14 3.094 23.682 0C25.384 18.573 15.177 0 12 0zm7.76 18.949c-1.008 2.028-14.493 2.05-15.514 0C3.224 16.9 9.92 4.755 12.003 4.755c2.081 0 8.77 12.166 7.759 14.196zM12 9.198c-1.054 0-4.446 6.15-3.93 7.189.518 1.04 7.348 1.027 7.86 0 .511-1.027-2.874-7.19-3.93-7.19z"/></svg>';
    const sourceBadge = `<div class="person-film-format-badge" style="background:rgba(20,20,28,0.82);display:flex;align-items:center;justify-content:center;padding:3px 5px;">${logo}</div>`;
    const formatBadge = _filmographyInCollection(m) && _filmographyFormat(m) ? `<div class="person-film-format-badge">${escHtml(_filmographyFormat(m))}</div>` : '';
    const rating = m.vote_average > 0 ? `<div class="person-film-rating">⭐ ${m.vote_average.toFixed(1)}</div>` : '';
    const role = m.character ? `<div class="person-film-year">${t('person.as')} ${escHtml(m.character)}</div>` : '';
    html += `<div class="person-film-card" style="${cursor}" ${onclick}>`;
    html += `<div style="position:relative">${poster}${formatBadge || sourceBadge}</div>`;
    html += `<div class="person-film-title">${escHtml(m.title)}</div>`;
    html += `<div class="person-film-year">${m.year || ''}</div>${rating}${role}`;
    html += `</div>`;
  });
  grid.innerHTML = html;
}

function _renderFilmographyGrid(data, sort) {
  const grid = document.getElementById('personFilmographyGrid');
  if (!grid) return;
  let movies = [...(data.cast || [])];
  if (sort === 'newest') movies.sort((a, b) => (b.year || '0').localeCompare(a.year || '0'));
  else if (sort === 'oldest') movies.sort((a, b) => (a.year || '9999').localeCompare(b.year || '9999'));
  else if (sort === 'az') movies.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
  else if (sort === 'rating') movies.sort((a, b) => (b.vote_average || 0) - (a.vote_average || 0));

  if (!movies.length) {
    grid.innerHTML = `<div style="color:var(--text-muted);font-size:0.85rem;">${t('person.noFilmography') || 'Geen filmografie beschikbaar.'}</div>`;
    return;
  }
  let html = '';
  movies.forEach(m => {
    const owned = _filmographyInCollection(m) || _filmographyInDigital(m);
    const localMovieId = _filmographyLocalMovieId(m);
    const onclick = _filmographyInCollection(m) && localMovieId ? `onclick="_detailReturnTab='person-detail';openMovieDetail(${localMovieId})"` : '';
    const dim = owned ? '' : 'opacity:0.45;';
    const cursor = _filmographyInCollection(m) && localMovieId ? '' : 'cursor:default;';
    const src = posterSrc(m);
    const poster = src
      ? `<img class="person-film-poster" src="${escHtml(src)}" onerror="this.style.display='none'" loading="lazy">`
      : `<div class="person-film-poster" style="display:flex;align-items:center;justify-content:center;font-size:1.5rem;color:var(--text-muted)">🎬</div>`;
    const formatBadge = _filmographyInCollection(m) && _filmographyFormat(m)
      ? `<div class="person-film-format-badge">${escHtml(_filmographyFormat(m))}</div>`
      : (_filmographyInDigital(m) ? (() => {
          const logo = _filmographyDigitalSource(m) === 'plex'
            ? '<svg viewBox="0 0 24 24" width="12" height="12" fill="#E5A00D"><path d="M3.987 8.409c-.96 0-1.587.28-2.12.933v-.72H0v8.88s.038.018.127.037c.138.03.821.187 1.331-.249.441-.377.542-.814.542-1.318v-1.283c.533.573 1.147.813 2 .813 1.84 0 3.253-1.493 3.253-3.48 0-2.12-1.36-3.613-3.266-3.613Zm16.748 5.595.406.591c.391.614.894.906 1.492.908.621-.012 1.064-.562 1.226-.755 0 0-.307-.27-.686-.72-.517-.614-1.214-1.755-1.24-1.803l-1.198 1.779Zm-3.205-1.955c0-2.08-1.52-3.64-3.52-3.64s-3.467 1.587-3.467 3.573a3.48 3.48 0 0 0 3.507 3.52c1.413 0 2.626-.84 3.253-2.293h-2.04l-.093.093c-.427.4-.72.533-1.227.533-.787 0-1.373-.506-1.453-1.266h4.986c.04-.214.054-.307.054-.52Zm-7.671-.219c0 .769.11 1.701.868 2.722l.056.069c-.306.526-.742.88-1.248.88-.399 0-.814-.211-1.138-.579a2.177 2.177 0 0 1-.538-1.441V6.409H9.86l-.001 5.421Zm9.283 3.46h-2.39l2.247-3.332-2.247-3.335h2.39l2.248 3.335-2.248 3.332Zm1.593-1.286Zm-17.162-.342c-.933 0-1.68-.773-1.68-1.72s.76-1.666 1.68-1.666c.92 0 1.68.733 1.68 1.68 0 .946-.733 1.706-1.68 1.706Zm18.361-1.974L24 8.622h-2.391l-.87 1.293 1.195 1.773Zm-9.404-.466c.16-.706.72-1.133 1.493-1.133.773 0 1.373.467 1.507 1.133h-3Z"/></svg>'
            : '<svg viewBox="0 0 24 24" width="12" height="12" fill="#00A4DC"><path d="M12 .002C8.826.002-1.398 18.537.16 21.666c1.56 3.129 22.14 3.094 23.682 0C25.384 18.573 15.177 0 12 0zm7.76 18.949c-1.008 2.028-14.493 2.05-15.514 0C3.224 16.9 9.92 4.755 12.003 4.755c2.081 0 8.77 12.166 7.759 14.196zM12 9.198c-1.054 0-4.446 6.15-3.93 7.189.518 1.04 7.348 1.027 7.86 0 .511-1.027-2.874-7.19-3.93-7.19z"/></svg>';
          return `<div class="person-film-format-badge" style="background:rgba(20,20,28,0.82);display:flex;align-items:center;justify-content:center;padding:3px 5px;">${logo}</div>`;
        })() : '');
    const ownedDot = owned ? `<div class="person-film-owned-dot"${_filmographyInDigital(m) && !_filmographyInCollection(m) ? ' style="background:var(--info,#3a8fd1)"' : ''}></div>` : '';
    const rating = m.vote_average > 0 ? `<div class="person-film-rating">⭐ ${m.vote_average.toFixed(1)}</div>` : '';
    const role = m.character ? `<div class="person-film-year">${t('person.as')} ${escHtml(m.character)}</div>` : '';
    html += `<div class="person-film-card" style="${dim}${cursor}" ${onclick}>`;
    html += `<div style="position:relative">${poster}${formatBadge}${ownedDot}</div>`;
    html += `<div class="person-film-title">${escHtml(m.title)}</div>`;
    html += `<div class="person-film-year">${m.year || ''}</div>${rating}${role}`;
    html += `</div>`;
  });
  grid.innerHTML = html;
}

function closePersonDetail() {
  currentPersonId = null;
  switchTabDirect(_personReturnPanel);
  if (_personReturnPanel === 'movie-detail') {
    _replaceRoute(`/movie/${currentMovieId}`);
  } else {
    _replaceRoute(_tabPath(_personReturnPanel));
  }
}

// Alias kept for legacy inline calls
function closePersonOverlay() { closePersonDetail(); }

async function copyValueToClipboard(value, statusElId) {
  if (!value) return;
  const statusEl = document.getElementById(statusElId);
  try {
    await navigator.clipboard.writeText(String(value));
    if (statusEl) {
      statusEl.style.color = 'var(--success)';
      statusEl.textContent = t('js.idCopied');
      setTimeout(() => {
        if (statusEl.textContent === t('js.idCopied')) statusEl.textContent = '';
      }, 1500);
    }
  } catch (e) {
    if (statusEl) {
      statusEl.style.color = 'var(--danger)';
      statusEl.textContent = t('js.copyFailed');
      setTimeout(() => {
        statusEl.style.color = 'var(--success)';
        if (statusEl.textContent === t('js.copyFailed')) statusEl.textContent = '';
      }, 1800);
    }
  }
}

function copyCurrentMovieId() {
  copyValueToClipboard(currentMovieId, 'modalMovieIdStatus');
}

function copyCurrentPersonId() {
  copyValueToClipboard(currentPersonId, 'personIdStatus');
}

async function deleteCurrentMovie() {
  const movie = allMovies.find(m => m.id === currentMovieId);
  if (!movie || !confirm(t('js.confirmDeleteCurrent', movie.title))) return;
  await fetch(`${API}/movies/${currentMovieId}`, { method: 'DELETE' });
  allMovies = allMovies.filter(m => m.id !== currentMovieId);
  closeModalDirect();
  filterMovies();
  loadStats();
}

async function refreshSingleMovie(btnEl) {
  const btn = btnEl || document.getElementById('btnRefreshSingle');
  const origText = btn.innerHTML;
  btn.innerHTML = t('js.fetching');
  btn.disabled = true;

  try {
    const r = await fetch(`${API}/movies/${currentMovieId}/sync-all`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fetch_posters: true })
    });
    const d = await r.json();

    if (d.status === 'updated') {
      const mr = await fetch(`${API}/movies/${currentMovieId}`);
      const fresh = await mr.json();
      const idx = allMovies.findIndex(m => m.id === currentMovieId);
      if (idx >= 0) allMovies[idx] = fresh;
      openMovieDetail(currentMovieId);
      filterMovies();
      btn.innerHTML = t('js.updated');
      btn.style.color = 'var(--success)';
      btn.style.borderColor = 'rgba(64,192,128,.4)';
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.style.color = '';
        btn.style.borderColor = '';
        btn.disabled = false;
      }, 2000);
    } else if (d.status === 'skipped') {
      btn.innerHTML = t('js.noNewData');
      btn.style.color = 'var(--accent)';
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.style.color = '';
        btn.disabled = false;
      }, 2000);
    } else {
      btn.innerHTML = t('js.errorShort');
      btn.style.color = 'var(--danger)';
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.style.color = '';
        btn.disabled = false;
      }, 2000);
    }
  } catch(e) {
    btn.innerHTML = t('js.errorShort');
    btn.style.color = 'var(--danger)';
    setTimeout(() => {
      btn.innerHTML = origText;
      btn.style.color = '';
      btn.disabled = false;
    }, 2000);
  }
}

async function syncSingleMovieSource(source, buttonId) {
  const btn = document.getElementById(buttonId);
  if (!btn) return;

  const origText = btn.innerHTML;
  const origColor = btn.style.color;
  const origBorder = btn.style.borderColor;

  btn.innerHTML = t('js.syncing');
  btn.disabled = true;

  try {
    const r = await fetch(`${API}/movies/${currentMovieId}/sync-source`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, fetch_posters: true })
    });
    const d = await r.json();

    if (d.status === 'updated') {
      const mr = await fetch(`${API}/movies/${currentMovieId}`);
      const fresh = await mr.json();
      const idx = allMovies.findIndex(m => m.id === currentMovieId);
      if (idx >= 0) allMovies[idx] = fresh;
      openMovieDetail(currentMovieId);
      filterMovies();

      btn.innerHTML = t('js.synced');
      btn.style.color = 'var(--success)';
      btn.style.borderColor = 'rgba(64,192,128,.4)';
    } else if (d.status === 'skipped') {
      btn.innerHTML = t('js.noSourceData');
      btn.style.color = 'var(--accent)';
      btn.style.borderColor = 'rgba(232,197,71,.4)';
    } else {
      btn.innerHTML = t('js.errorShort');
      btn.style.color = 'var(--danger)';
      btn.style.borderColor = 'rgba(240,64,96,.4)';
    }
  } catch(e) {
    btn.innerHTML = t('js.errorShort');
    btn.style.color = 'var(--danger)';
    btn.style.borderColor = 'rgba(240,64,96,.4)';
  }

  setTimeout(() => {
    btn.innerHTML = origText;
    btn.style.color = origColor;
    btn.style.borderColor = origBorder;
    btn.disabled = false;
  }, 2000);
}

// ── Edit mode ─────────────────────────────────────────────────────────────────

// Mapping: edit field id suffix → movie object key
const EDIT_FIELDS = {
  Title:              'title',
  OriginalTitle:      'original_title',
  SortTitle:          'sort_title',
  Year:               'year',
  ReleaseDate:        'release_date',
  Director:           'director',
  Actor:              'actor',
  Producer:           'producer',
  Studios:            'studios',
  Genre:              'genre',
  Format:             'format',
  EditionType:        'edition_type',
  CustomEditionLabel: 'custom_edition_label',
  Runtime:            'runtime',
  Hdr:                'hdr',
  ScreenRatios:       'screen_ratios',
  Packaging:          'packaging',
  Regions:            'regions',
  AudienceRating:     'audience_rating',
  Rating:             'rating',
  AudioTracks:        'audio_tracks',
  Subtitles:          'subtitles',
  Distributor:        'distributor',
  Country:            'country',
  Language:           'language',
  BoxSet:             'box_set',
  Edition:            'edition',
  EditionReleaseYear: 'edition_release_year',
  EditionReleaseDate: 'edition_release_date',
  PurchasePrice:      'purchase_price',
  PurchaseDate:       'purchase_date',
  Location:           'location',
  Extras:             'extras',
  Plot:               'plot',
  Notes:              'notes',
  Poster:             'poster',
  ImdbId:             'imdb_id',
  ImdbUrl:            'imdb_url',
  TrailerUrl:         'trailer_url',
};

async function _populateGroupCheckboxes(currentGroupIds) {
  const container = document.getElementById('editGroupsContainer');
  if (!container) return;
  container.innerHTML = '';
  try {
    const r = await fetch(`${API}/groups`);
    const groups = await r.json();
    if (!groups.length) {
      container.innerHTML = '<span style="color:var(--text-muted); font-size:0.82rem;">' + t('js.noGroups') + '</span>';
      return;
    }
    const ids = currentGroupIds || [];
    for (const g of groups) {
      const checked = ids.includes(g.id) ? 'checked' : '';
      container.insertAdjacentHTML('beforeend', `
        <label style="display:flex; align-items:center; gap:6px; padding:6px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; cursor:pointer; font-size:0.84rem; white-space:nowrap;">
          <input type="checkbox" class="edit-group-cb" value="${g.id}" ${checked} style="accent-color:var(--accent); width:15px; height:15px;">
          ${g.name}
        </label>
      `);
    }
  } catch(e) {}
}

function _checkedEditContainerIds(selector) {
  return [...document.querySelectorAll(selector + ':checked')]
    .map(cb => parseInt(cb.value, 10))
    .filter(Number.isFinite);
}

function _editContainerSnapshot() {
  return JSON.stringify({
    vault_ids: _checkedEditContainerIds('.edit-vault-cb').sort((a, b) => a - b),
    box_set_ids: _checkedEditContainerIds('.edit-boxset-cb').sort((a, b) => a - b),
    collection_ids: _checkedEditContainerIds('.edit-collection-cb').sort((a, b) => a - b),
  });
}

function _renderEditContainerChecks(targetId, items, checkedIds, className, emptyText) {
  const target = document.getElementById(targetId);
  if (!target) return;
  const checked = new Set((checkedIds || []).map(Number));
  if (!items.length) {
    target.innerHTML = `<span style="color:var(--text-muted); font-size:0.82rem;">${emptyText}</span>`;
    return;
  }
  target.innerHTML = items.map(item => `
    <label style="display:flex; align-items:center; gap:6px; padding:6px 10px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; cursor:pointer; font-size:0.82rem; white-space:nowrap;">
      <input type="checkbox" class="${className}" value="${item.id}" ${checked.has(Number(item.id)) ? 'checked' : ''} onchange="_editDirty=true" style="accent-color:var(--accent); width:15px; height:15px;">
      ${escHtml(item.title || ('#' + item.id))}
    </label>
  `).join('');
}

async function _populateEditContainerSelectors(movie) {
  const containers = movie._containers || {};
  const checkedVaultIds = (containers.vaults || []).map(v => v.id);
  const checkedBoxSetIds = (containers.box_sets || []).map(bs => bs.id);
  const checkedCollectionIds = (containers.collections_direct || []).map(c => c.id);

  if (movie.edition_group_id && !checkedVaultIds.includes(movie.edition_group_id)) checkedVaultIds.push(movie.edition_group_id);
  if (movie.super_group_id && !checkedBoxSetIds.includes(movie.super_group_id)) checkedBoxSetIds.push(movie.super_group_id);
  if (movie.collection_id && !checkedCollectionIds.includes(movie.collection_id)) checkedCollectionIds.push(movie.collection_id);

  try {
    const [egRes, colRes] = await Promise.all([
      fetch(`${API}/edition-groups`),
      fetch(`${API}/collections`)
    ]);
    const egs = await egRes.json();
    const cols = await colRes.json();
    const vaults = egs.filter(g => !_isBoxSetGroup(g));
    const boxSets = egs.filter(g => _isBoxSetGroup(g));
    _renderEditContainerChecks('editVaultsContainer', vaults, checkedVaultIds, 'edit-vault-cb', t('js.noGroups'));
    _renderEditContainerChecks('editBoxSetsContainer', boxSets, checkedBoxSetIds, 'edit-boxset-cb', t('js.noGroups'));
    _renderEditContainerChecks('editCollectionsContainer', cols, checkedCollectionIds, 'edit-collection-cb', t('settings.groupMgmtEmpty', 'Geen groepen gevonden.'));
  } catch (e) {
    ['editVaultsContainer', 'editBoxSetsContainer', 'editCollectionsContainer'].forEach(id => {
      const target = document.getElementById(id);
      if (target) target.innerHTML = `<span style="color:var(--danger); font-size:0.82rem;">${t('js.groupsLoadError')}</span>`;
    });
  }
}

async function startEdit() {
  let movie = allMovies.find(m => m.id === currentMovieId);
  if (!movie) return;
  if (!movie._containers) {
    try {
      const r = await fetch(`${API}/movies/${currentMovieId}`);
      if (r.ok) {
        movie = await r.json();
        const idx = allMovies.findIndex(m => m.id === currentMovieId);
        if (idx >= 0) allMovies[idx] = { ...allMovies[idx], ...movie };
      }
    } catch (e) {}
  }

  // Admin editing another user's movie → ask confirmation
  if (authEnabled && currentUserRole === 'admin' && movie.owner_id && movie.owner_id !== currentUserId) {
    const ownerName = movie.owner_name || ('user #' + movie.owner_id);
    if (!confirm(t('js.confirmEditOther', ownerName))) return;
  }

  // Populate all edit fields
  for (const [suffix, key] of Object.entries(EDIT_FIELDS)) {
    const el = document.getElementById('edit' + suffix);
    if (el) {
      if (el.tagName === 'SELECT') {
        el.value = movie[key] || el.options[0].value;
      } else {
        el.value = movie[key] || '';
      }
    }
  }

  // Populate group checkboxes
  _populateGroupCheckboxes(movie.group_ids || []);
  _renderEditContainerSummary(movie);
  await _populateEditContainerSelectors(movie);

  document.getElementById('editStatus').className = 'status-msg';
  switchEditTab('general'); // initialize tab state before showing modal
  document.getElementById('modalViewMode').style.display = 'none';
  document.getElementById('modalEditMode').style.display = 'block';
  toggleCustomEditionInput(); // Show/hide custom label input based on current edition type

  // Populate extra videos list
  _renderEditVideosList(movie.videos || '');

  // Snapshot original values for dirty-checking
  _editSnapshot = {};
  for (const [suffix] of Object.entries(EDIT_FIELDS)) {
    const el = document.getElementById('edit' + suffix);
    if (el) _editSnapshot[suffix] = el.value;
  }
  _editSnapshot._groups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
  _editSnapshot._containers = _editContainerSnapshot();
  _editDirty = false;
}

let _editSnapshot = {};
let _editDirty = false;

function _isEditDirty() {
  if (_editDirty) return true;
  for (const [suffix] of Object.entries(EDIT_FIELDS)) {
    const el = document.getElementById('edit' + suffix);
    if (el && el.value !== (_editSnapshot[suffix] ?? '')) return true;
  }
  const curGroups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
  if (curGroups !== (_editSnapshot._groups ?? '')) return true;
  if (_editContainerSnapshot() !== (_editSnapshot._containers ?? '')) return true;
  return false;
}

function cancelEdit() {
  if (_isEditDirty()) {
    if (confirm(t('js.unsavedChanges'))) { saveEdit(); return; }
  }
  _editDirty = false;
  document.getElementById('modalEditMode').style.display = 'none';
  document.getElementById('modalViewMode').style.display = '';
}

async function saveEdit() {
  const btn = document.getElementById('btnSaveEdit');
  btn.innerHTML = t('js.saving');
  btn.disabled = true;

  const payload = {};
  for (const [suffix, key] of Object.entries(EDIT_FIELDS)) {
    const el = document.getElementById('edit' + suffix);
    if (el) payload[key] = el.value;
  }
  // Extra videos JSON
  payload.videos = _collectEditVideos();
  const containerPayload = {
    vault_ids: _checkedEditContainerIds('.edit-vault-cb'),
    box_set_ids: _checkedEditContainerIds('.edit-boxset-cb'),
    collection_ids: _checkedEditContainerIds('.edit-collection-cb'),
  };

  // Collect selected group IDs from checkboxes
  const selectedGroupIds = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => parseInt(cb.value));
  const previousContainerSnapshot = _editSnapshot._containers ?? '';

  try {
    const r = await fetch(`${API}/movies/${currentMovieId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (r.ok) {
      let updatedContainers = null;
      const cr = await fetch(`${API}/movies/${currentMovieId}/containers`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(containerPayload)
      });
      const cd = await cr.json();
      if (!cr.ok) {
        showStatus('editStatus', cd.error || t('js.saveError'), 'error');
        btn.innerHTML = t('js.saveBtn');
        btn.disabled = false;
        return;
      }
      updatedContainers = cd.containers || null;
      // Update group assignments
      await fetch(`${API}/movies/${currentMovieId}/groups`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_ids: selectedGroupIds })
      });
      if (d && typeof d === 'object') d.group_ids = selectedGroupIds;
      if (d.queued) {
        showStatus('editStatus', t('js.queuedEdit'), 'info');
        // Update local cache so re-opening edit shows the queued values
        const idx = allMovies.findIndex(m => m.id === currentMovieId);
        if (idx >= 0) allMovies[idx] = { ...allMovies[idx], ...payload, ...cd.movie, _containers: updatedContainers, group_ids: selectedGroupIds };
        filterMovies();
        // Reset dirty state so cancel/back doesn't prompt about unsaved changes
        _editSnapshot = {};
        for (const [suffix] of Object.entries(EDIT_FIELDS)) {
          const el = document.getElementById('edit' + suffix);
          if (el) _editSnapshot[suffix] = el.value;
        }
        _editSnapshot._groups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
        _editSnapshot._containers = _editContainerSnapshot();
        _editDirty = false;
      } else {
        // Update local cache
        const idx = allMovies.findIndex(m => m.id === currentMovieId);
        if (idx >= 0) allMovies[idx] = { ...allMovies[idx], ...d, ...cd.movie, _containers: updatedContainers };

        showStatus('editStatus', t('js.saved'), 'success');
        // Stay in edit mode — update snapshot so fields are no longer dirty
        _editSnapshot = {};
        for (const [suffix] of Object.entries(EDIT_FIELDS)) {
          const el = document.getElementById('edit' + suffix);
          if (el) _editSnapshot[suffix] = el.value;
        }
        _editSnapshot._groups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
        _editSnapshot._containers = _editContainerSnapshot();
        _editDirty = false;
        // Reload full collection when normalized container links changed in grouped view.
        if (groupEditionsEnabled && _editSnapshot._containers !== previousContainerSnapshot) {
          loadCollection();
        } else {
          filterMovies();  // Update grid
        }
      }
    } else {
      showStatus('editStatus', d.error || t('js.saveError'), 'error');
    }
  } catch(e) {
    showStatus('editStatus', t('js.error', e.message), 'error');
  }
  btn.innerHTML = t('js.saveBtn');
  btn.disabled = false;
}

function _renderEditVideosList(videosJson) {
  const list = document.getElementById('editVideosList');
  if (!list) return;
  list.innerHTML = '';
  let videos = [];
  try { videos = videosJson ? JSON.parse(videosJson) : []; } catch(e) {}
  videos.forEach((v, i) => {
    list.insertAdjacentHTML('beforeend', _videoEntryHtml(v.url || '', v.label || '', v.type || '', v.source || 'manual', i));
  });
}

const _VIDEO_TYPES = ['Trailer', 'Teaser', 'Clip', 'Featurette', 'Behind the Scenes', 'Bloopers'];

function _videoEntryHtml(url, label, videoType, source, idx) {
  const typeOpts = ['', ..._VIDEO_TYPES].map(opt =>
    `<option value="${opt}"${videoType === opt ? ' selected' : ''}>${opt || t('modal.videoTypeOther')}</option>`
  ).join('');
  const srcOpts = ['manual', 'tmdb'].map(opt =>
    `<option value="${opt}"${source === opt ? ' selected' : ''}>${opt === 'tmdb' ? 'TMDb' : t('edit.videoSourceManual')}</option>`
  ).join('');
  return `<div class="edit-video-entry" data-idx="${idx}" style="display:flex;gap:8px;align-items:center;margin-bottom:6px;flex-wrap:wrap;">
    <input type="text" class="ev-url" value="${url.replace(/"/g, '&quot;')}" placeholder="YouTube URL" style="flex:2;min-width:180px;">
    <input type="text" class="ev-label" value="${label.replace(/"/g, '&quot;')}" placeholder="${t('edit.videoLabelPlaceholder')}" style="flex:1;min-width:110px;">
    <select class="ev-type" style="flex:1;min-width:120px;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 6px;font-size:0.82rem;">${typeOpts}</select>
    <select class="ev-source" style="min-width:90px;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 6px;font-size:0.82rem;">${srcOpts}</select>
    <button class="btn btn-secondary" type="button" style="padding:5px 10px;min-width:unset;" onclick="this.closest('.edit-video-entry').remove()">✕</button>
  </div>`;
}

function addVideoEntry() {
  const list = document.getElementById('editVideosList');
  if (!list) return;
  const urlInput = document.getElementById('editVideoUrl');
  const labelInput = document.getElementById('editVideoLabel');
  const typeInput = document.getElementById('editVideoType');
  const url = urlInput ? urlInput.value.trim() : '';
  const label = labelInput ? labelInput.value.trim() : '';
  const videoType = typeInput ? typeInput.value : '';
  if (!url) {
    if (urlInput) { urlInput.style.outline = '2px solid var(--danger, #e05)'; setTimeout(() => { urlInput.style.outline = ''; }, 1200); }
    return;
  }
  const idx = list.querySelectorAll('.edit-video-entry').length;
  list.insertAdjacentHTML('beforeend', _videoEntryHtml(url, label, videoType, 'manual', idx));
  if (urlInput) urlInput.value = '';
  if (labelInput) labelInput.value = '';
  if (typeInput) typeInput.value = '';
  _editDirty = true;
}

function _collectEditVideos() {
  const list = document.getElementById('editVideosList');
  if (!list) return '';
  const entries = [...list.querySelectorAll('.edit-video-entry')].map(row => ({
    url:    row.querySelector('.ev-url')?.value?.trim() || '',
    label:  row.querySelector('.ev-label')?.value?.trim() || '',
    type:   row.querySelector('.ev-type')?.value || '',
    source: row.querySelector('.ev-source')?.value || 'manual',
  })).filter(v => v.url);
  return entries.length ? JSON.stringify(entries) : '';
}

async function uploadCustomCover() {
  const input = document.getElementById('editPosterUpload');
  const file = input && input.files ? input.files[0] : null;
  if (!file) {
    showStatus('editStatus', t('js.chooseCover'), 'error');
    return;
  }

  const fd = new FormData();
  fd.append('poster', file);

  showStatus('editStatus', t('js.uploadingCover'), 'info');

  try {
    const r = await fetch(`${API}/movies/${currentMovieId}/poster`, {
      method: 'POST',
      body: fd
    });
    const d = await r.json();
    if (!r.ok) {
      showStatus('editStatus', d.error || t('js.uploadFailed'), 'error');
      return;
    }

    const updated = d.movie || null;
    if (updated) {
      const idx = allMovies.findIndex(m => m.id === currentMovieId);
      if (idx >= 0) allMovies[idx] = updated;
    }

    const current = allMovies.find(m => m.id === currentMovieId);
    if (current && current.poster_file) {
      document.getElementById('editPoster').value = current.poster || '';
      openMovieDetail(currentMovieId);
      filterMovies();
    }

    input.value = '';
    showStatus('editStatus', t('js.coverUploaded'), 'success');
  } catch(e) {
    showStatus('editStatus', t('js.error', e.message), 'error');
  }
}

// ── Manual Add ────────────────────────────────────────────────────────────────
let currentAddBoxSetProposal = null;
let addTitleLookupResolvedTitle = '';
let addBarcodeLookupResolvedBarcode = '';
let addMetadataCandidateMap = {};

function _escapeAddHtml(value) {
  return String(value || '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[ch]));
}

function hideAddBoxSetProposal() {
  currentAddBoxSetProposal = null;
  const panel = document.getElementById('addBoxSetProposal');
  if (panel) panel.style.display = 'none';
  updateAddSubmitButtonState();
}

function updateAddSubmitButtonState() {
  const btn = document.getElementById('addSubmitBtn');
  if (!btn) return;
  if (currentAddBoxSetProposal) {
    btn.setAttribute('data-i18n', 'scan.boxSetProposalSave');
    btn.textContent = t('scan.boxSetProposalSave');
  } else {
    btn.setAttribute('data-i18n', 'add.submit');
    btn.textContent = t('add.submit');
  }
}

function updateAddIdentityLock() {
  const barcodeEl = document.getElementById('addBarcode');
  const titleEl = document.getElementById('addTitle');
  if (!barcodeEl || !titleEl) return;
  const barcode = barcodeEl.value.trim();
  const title = titleEl.value.trim();
  const useBarcode = !!barcode;
  const useTitle = !barcode && !!title;
  barcodeEl.disabled = useTitle;
  titleEl.disabled = useBarcode;
  barcodeEl.classList.toggle('input-muted', useTitle);
  titleEl.classList.toggle('input-muted', useBarcode);
}

function handleAddBarcodeInput() {
  addBarcodeLookupResolvedBarcode = '';
  addTitleLookupResolvedTitle = '';
  updateAddIdentityLock();
}

function handleAddTitleInput() {
  addBarcodeLookupResolvedBarcode = '';
  addTitleLookupResolvedTitle = '';
  updateAddIdentityLock();
}

function _addBoxSetMemberPosterSrc(movie) {
  return posterSrc(movie) || '';
}

function displayAddBoxSetProposal(proposal, barcode = '') {
  const movies = (proposal && proposal.movies ? proposal.movies : []).filter(m => m && m.title).slice(0, 50);
  if (!movies.length) {
    hideAddBoxSetProposal();
    return false;
  }
  currentAddBoxSetProposal = {
    box_set_title: proposal.title || document.getElementById('addTitle').value.trim() || `Box Set ${barcode}`,
    barcode,
    format: document.getElementById('addFormat').value || '4K UHD',
    source: proposal.source || 'Blu-ray.com',
    detail_url: proposal.detail_url || '',
    movies,
    selected_indices: movies.map((_, idx) => idx),
    saved_indices: []
  };

  const nameEl = document.getElementById('addBoxSetProposalName');
  const listEl = document.getElementById('addBoxSetProposalList');
  const panel = document.getElementById('addBoxSetProposal');
  const btn = document.getElementById('btnSaveAddBoxSetProposal');
  if (!nameEl || !listEl || !panel || !btn) return false;

  document.getElementById('addTmdbCandidates').style.display = 'none';
  nameEl.textContent = '';
  listEl.innerHTML = movies.map((m, idx) => `
    <div class="boxset-member-row">
      <input class="boxset-member-check" type="checkbox" id="addBoxSetMovieSelect${idx}" checked onchange="toggleAddBoxSetProposalMovie(${idx}, this.checked)" aria-label="${_escapeAddHtml(m.title)}">
      <span class="boxset-member-index">${idx + 1}</span>
      <span class="boxset-member-cover">${_addBoxSetMemberPosterSrc(m) ? `<img src="${_escapeAddHtml(_addBoxSetMemberPosterSrc(m))}" alt="">` : '🎬'}</span>
      <strong class="boxset-member-title">${_escapeAddHtml(m.title)}</strong>
      ${m.year ? `<span class="tag">${_escapeAddHtml(m.year)}</span>` : ''}
      <button class="btn btn-secondary boxset-member-save" id="addBoxSetMovieBtn${idx}" onclick="saveAddBoxSetProposalMovie(${idx})">Opslaan</button>
    </div>
  `).join('');
  btn.disabled = false;
  btn.textContent = t('scan.boxSetProposalSave');
  panel.style.display = 'block';
  updateAddBoxSetProposalSelection();
  updateAddSubmitButtonState();
  return true;
}

function updateAddBoxSetProposalSelection() {
  if (!currentAddBoxSetProposal) return;
  const selectedCount = currentAddBoxSetProposal.selected_indices
    .filter(idx => !currentAddBoxSetProposal.saved_indices.includes(idx)).length;
  const nameEl = document.getElementById('addBoxSetProposalName');
  if (nameEl) {
    nameEl.textContent = `${currentAddBoxSetProposal.box_set_title} · ${selectedCount} films gevonden`;
  }
  const saveAllBtn = document.getElementById('btnSaveAddBoxSetProposal');
  if (saveAllBtn) {
    saveAllBtn.disabled = selectedCount === 0;
  }
  currentAddBoxSetProposal.movies.forEach((_, idx) => {
    const rowBtn = document.getElementById(`addBoxSetMovieBtn${idx}`);
    if (rowBtn && !currentAddBoxSetProposal.saved_indices.includes(idx)) {
      rowBtn.disabled = !currentAddBoxSetProposal.selected_indices.includes(idx);
    }
  });
}

function toggleAddBoxSetProposalMovie(index, checked) {
  if (!currentAddBoxSetProposal) return;
  if (checked) {
    if (!currentAddBoxSetProposal.selected_indices.includes(index)) {
      currentAddBoxSetProposal.selected_indices.push(index);
    }
  } else {
    currentAddBoxSetProposal.selected_indices = currentAddBoxSetProposal.selected_indices.filter(idx => idx !== index);
  }
  updateAddBoxSetProposalSelection();
}

async function lookupAddBoxSetProposal(movie, barcode = '') {
  const title = (movie && (movie.title || movie.original_title)) || document.getElementById('addTitle').value.trim();
  const year = (movie && movie.year) || document.getElementById('addYear').value.trim();
  if (!title && !barcode) return null;
  const params = new URLSearchParams();
  if (title) params.set('title', title);
  if (year) params.set('year', year);
  if (barcode) params.set('barcode', barcode);
  try {
    const r = await fetch(`${API}/box-set-proposals/lookup?${params.toString()}`);
    const d = await r.json();
    if (r.ok && d.status === 'found' && d.box_set_proposal) return d.box_set_proposal;
  } catch(e) { /* best-effort enrichment */ }
  return null;
}

async function _saveAddBoxSetProposalMovies(indices) {
  if (!currentAddBoxSetProposal) return;
  const movies = indices
    .filter(idx => !currentAddBoxSetProposal.saved_indices.includes(idx))
    .map(idx => currentAddBoxSetProposal.movies[idx])
    .filter(Boolean);
  if (!movies.length) return null;
  const payload = {
    ...currentAddBoxSetProposal,
    movies,
    box_set_id: currentAddBoxSetProposal.box_set_id || null
  };
  const r = await fetch(`${API}/box-set-proposals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || t('js.saveFailed'));
  currentAddBoxSetProposal.box_set_id = d.box_set.id;
  indices.forEach(idx => {
    if (!currentAddBoxSetProposal.saved_indices.includes(idx)) {
      currentAddBoxSetProposal.saved_indices.push(idx);
    }
    const rowBtn = document.getElementById(`addBoxSetMovieBtn${idx}`);
    if (rowBtn) {
      rowBtn.disabled = true;
      rowBtn.textContent = 'Opgeslagen';
      rowBtn.classList.add('is-saved');
    }
    const rowCheck = document.getElementById(`addBoxSetMovieSelect${idx}`);
    if (rowCheck) {
      rowCheck.checked = true;
      rowCheck.disabled = true;
    }
  });
  updateAddBoxSetProposalSelection();
  if (typeof loadStats === 'function') loadStats();
  if (typeof loadCollection === 'function') loadCollection();
  return d;
}

async function saveAddBoxSetProposalMovie(index) {
  const btn = document.getElementById(`addBoxSetMovieBtn${index}`);
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
  }
  try {
    const d = await _saveAddBoxSetProposalMovies([index]);
    showStatus('addStatus', t('scan.boxSetProposalSaved', d.box_set.title, d.movies.length), 'success');
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Opslaan';
    }
  }
}

async function saveAddBoxSetProposal() {
  if (!currentAddBoxSetProposal) return;
  const btn = document.getElementById('btnSaveAddBoxSetProposal');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> ' + t('scan.boxSetProposalSaving');
  }
  try {
    const indices = currentAddBoxSetProposal.selected_indices.slice();
    const d = await _saveAddBoxSetProposalMovies(indices);
    showStatus('addStatus', t('scan.boxSetProposalSaved', d.box_set.title, d.movies.length), 'success');
    hideAddBoxSetProposal();
    clearManualForm();
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('scan.boxSetProposalSave');
    }
  }
}

async function autoFillFromTitle() {
  const title = document.getElementById('addTitle').value.trim();
  if (!title) return;
  const year = document.getElementById('addYear').value.trim();
  showStatus('addStatus', t('js.searchingMovie'), 'info');
  document.getElementById('addTmdbCandidates').style.display = 'none';
  hideAddBoxSetProposal();
  try {
    const params = new URLSearchParams({ q: title });
    if (year) params.set('year', year);
    const r = await fetch(`${API}/search_title?${params.toString()}`);
    const d = await r.json();
    if (!r.ok) {
      showStatus('addStatus', d.error || t('js.backendError', r.status), 'error');
      return;
    }
    if (Array.isArray(d.metadata_candidates) && d.metadata_candidates.length) {
      _showAddCandidates(d.metadata_candidates);
      addTitleLookupResolvedTitle = '';
      showStatus('addStatus', t('scan.otherMatches'), 'info');
      return;
    }
    if (d.status !== 'found' || !d.movie) {
      showStatus('addStatus', t('js.noInfoFound'), 'error');
      return;
    }
    _fillAddFields(d.movie);
    const proposal = await lookupAddBoxSetProposal(d.movie);
    if (proposal) displayAddBoxSetProposal(proposal);
    else if (Array.isArray(d.tmdb_candidates) && d.tmdb_candidates.length > 1) {
      _showAddCandidates(d.tmdb_candidates);
    }
    addTitleLookupResolvedTitle = document.getElementById('addTitle').value.trim();
    showStatus('addStatus', t('js.infoFound', d.movie.title || title), 'success');
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
  }
}

function _showAddCandidatesLegacy(candidates) {
  const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  hideAddBoxSetProposal();
  document.getElementById('addCandidateList').innerHTML = candidates.map(c => `
    <div class="tmdb-candidate-card" onclick="_selectAddCandidate('${c.tmdb_id}')">
      <div class="tmdb-candidate-poster">
        ${c.poster ? `<img src="${esc(c.poster)}" loading="lazy" alt="">` : '<div class="no-poster">🎬</div>'}
      </div>
      <div class="tmdb-candidate-info">
        <strong>${esc(c.title)}${c.year ? ` <span class="tag">${esc(c.year)}</span>` : ''}</strong>
        ${c.vote_average ? `<div class="tmdb-candidate-vote">⭐ ${Number(c.vote_average).toFixed(1)}</div>` : ''}
        <p>${esc(c.overview)}</p>
      </div>
    </div>
  `).join('');
  document.getElementById('addTmdbCandidates').style.display = 'block';
}

async function _selectAddCandidateTmdbLegacy(tmdbId) {
  document.getElementById('addTmdbCandidates').style.display = 'none';
  const existingBarcode = document.getElementById('addBarcode').value.trim();
  hideAddBoxSetProposal();
  showStatus('addStatus', t('js.lookingUp'), 'info');
  try {
    const r = await fetch(`${API}/tmdb_movie/${tmdbId}`);
    const d = await r.json();
    if (!r.ok || !d.movie) {
      showStatus('addStatus', d.error || t('js.backendError', r.status), 'error');
      return;
    }
    _fillAddFields(d.movie);
    if (existingBarcode) {
      addBarcodeLookupResolvedBarcode = existingBarcode;
      addTitleLookupResolvedTitle = '';
    } else {
      addTitleLookupResolvedTitle = document.getElementById('addTitle').value.trim();
    }
    const proposal = await lookupAddBoxSetProposal(d.movie, existingBarcode);
    if (proposal && displayAddBoxSetProposal(proposal, existingBarcode)) {
      showStatus('addStatus', t('js.infoFound', d.movie.title), 'success');
      return;
    }
    showStatus('addStatus', t('js.infoFound', d.movie.title), 'success');
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
  }
}

function _showAddCandidates(candidates) {
  const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  addMetadataCandidateMap = {};
  hideAddBoxSetProposal();
  document.getElementById('addCandidateList').innerHTML = (candidates || []).map((c, idx) => {
    const key = `candidate-${idx}`;
    const provider = c.provider_label || c.provider || 'Metadata';
    addMetadataCandidateMap[key] = c;
    return `
    <div class="tmdb-candidate-card" onclick="_selectAddCandidate('${key}')">
      <div class="tmdb-candidate-poster">
        ${c.poster ? `<img src="${esc(c.poster)}" loading="lazy" alt="">` : '<div class="no-poster">Movie</div>'}
        <span class="metadata-provider-badge">${esc(provider)}</span>
      </div>
      <div class="tmdb-candidate-info">
        <strong>${esc(c.title)}${c.year ? ` <span class="tag">${esc(c.year)}</span>` : ''}</strong>
        ${c.vote_average ? `<div class="tmdb-candidate-vote">Rating ${Number(c.vote_average).toFixed(1)}</div>` : ''}
        <p>${esc(c.overview)}</p>
      </div>
    </div>
  `;
  }).join('');
  document.getElementById('addTmdbCandidates').style.display = 'block';
}

async function _selectAddCandidate(candidateKey) {
  document.getElementById('addTmdbCandidates').style.display = 'none';
  const existingBarcode = document.getElementById('addBarcode').value.trim();
  hideAddBoxSetProposal();
  showStatus('addStatus', t('js.lookingUp'), 'info');
  try {
    const candidate = addMetadataCandidateMap[candidateKey] || {};
    let movie = candidate.movie || null;
    if (!movie && candidate.tmdb_id) {
      const r = await fetch(`${API}/tmdb_movie/${candidate.tmdb_id}`);
      const d = await r.json();
      if (!r.ok || !d.movie) {
        showStatus('addStatus', d.error || t('js.backendError', r.status), 'error');
        return;
      }
      movie = d.movie;
    }
    if (!movie) {
      movie = {
        title: candidate.title || '',
        year: candidate.year || '',
        plot: candidate.overview || '',
        poster: candidate.poster || '',
        tmdb_id: candidate.tmdb_id || '',
        imdb_id: candidate.imdb_id || ''
      };
    }
    _fillAddFields(movie);
    if (existingBarcode) {
      addBarcodeLookupResolvedBarcode = existingBarcode;
      addTitleLookupResolvedTitle = '';
    } else {
      addTitleLookupResolvedTitle = document.getElementById('addTitle').value.trim();
    }
    const proposal = await lookupAddBoxSetProposal(movie, existingBarcode);
    if (proposal && displayAddBoxSetProposal(proposal, existingBarcode)) {
      showStatus('addStatus', t('js.infoFound', movie.title), 'success');
      return;
    }
    showStatus('addStatus', t('js.infoFound', movie.title), 'success');
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
  }
}

async function _fillAddFormFromTmdbId(tmdbId) {
  hideAddBoxSetProposal();
  showStatus('addStatus', t('js.lookingUp'), 'info');
  try {
    const r = await fetch(`${API}/tmdb_movie/${tmdbId}`);
    const d = await r.json();
    if (r.ok && d.movie) {
      _fillAddFields(d.movie);
      const proposal = await lookupAddBoxSetProposal(d.movie);
      if (proposal) displayAddBoxSetProposal(proposal);
      showStatus('addStatus', t('js.infoFound', d.movie.title), 'success');
    } else {
      showStatus('addStatus', d.error || t('js.backendError', r.status), 'error');
    }
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
  }
}

function _fillAddFields(movie) {
  document.getElementById('addTitle').value          = movie.title          || '';
  document.getElementById('addOriginalTitle').value  = movie.original_title || '';
  if (movie.format) { const s = document.getElementById('addFormat'); if (s) s.value = movie.format; }
  document.getElementById('addYear').value           = movie.year           || '';
  document.getElementById('addReleaseDate').value    = movie.release_date   || '';
  document.getElementById('addDirector').value       = movie.director       || '';
  document.getElementById('addActor').value          = movie.actor          || '';
  document.getElementById('addProducer').value       = movie.producer       || '';
  document.getElementById('addStudios').value        = movie.studios        || '';
  document.getElementById('addGenre').value          = movie.genre          || '';
  document.getElementById('addRuntime').value        = movie.runtime        || '';
  document.getElementById('addRating').value         = movie.rating         || '';
  document.getElementById('addHdr').value            = movie.hdr            || '';
  document.getElementById('addLanguage').value       = movie.language       || '';
  document.getElementById('addAudioTracks').value    = movie.audio_tracks   || '';
  document.getElementById('addSubtitles').value      = movie.subtitles      || '';
  document.getElementById('addCountry').value        = movie.country        || '';
  document.getElementById('addPlot').value           = movie.plot           || '';
  document.getElementById('addImdbId').value         = movie.imdb_id        || '';
  document.getElementById('addImdbUrl').value        = movie.imdb_url || (movie.imdb_id ? 'https://www.imdb.com/title/' + movie.imdb_id : '');
  document.getElementById('addPosterHidden').value   = movie.poster         || '';
  if (movie.tmdb_id) {
    const tid = String(movie.tmdb_id);
    document.getElementById('addTmdbIdHidden').value  = tid;
    document.getElementById('addTmdbIdVisible').value = tid;
    document.getElementById('addTmdbUrl').value       = 'https://www.themoviedb.org/movie/' + tid;
  }
  updateAddIdentityLock();
}

async function _lookupBarcodeForAdd(barcode) {
  showStatus('addStatus', t('js.lookingUp'), 'info');
  document.getElementById('addTmdbCandidates').style.display = 'none';
  hideAddBoxSetProposal();
  try {
    const r = await fetch(`${API}/lookup/${barcode}?stream=1`);
    let finalData = null;
    await readLookupNdjson(r, (msg) => {
        if (msg.type === 'step') {
          const icon = msg.status === 'searching' ? '<span class="spinner"></span>' : msg.status === 'hit' ? '✓' : '—';
          showStatus('addStatus', `${icon} ${msg.source}${msg.detail ? ': ' + msg.detail : ''}`, 'info');
        } else if (msg.type === 'done') { finalData = msg; }
    });
    if (!finalData || finalData.error) {
      showStatus('addStatus', finalData?.error || t('js.backendError', ''), 'error'); return;
    }
    if (finalData.status === 'movie_exists' || finalData.status === 'exists') {
      showStatus('addStatus', t('js.alreadyInCollection', finalData.movie.title), 'success'); return;
    }
    if (finalData.status === 'box_set_exists') {
      showStatus('addStatus', t('js.alreadyInCollection', finalData.box_set?.title || barcode), 'success'); return;
    }
    if (finalData.status === 'vault_exists') {
      showStatus('addStatus', t('js.alreadyInCollection', finalData.vault?.title || barcode), 'success'); return;
    }
    if (finalData.status === 'not_found') {
      showStatus('addStatus', t('js.movieNotFound', barcode), 'error'); return;
    }
    const movie = finalData.movie;
    if (finalData.detected_format && !movie.format) movie.format = finalData.detected_format;
    _fillAddFields(movie);
    addBarcodeLookupResolvedBarcode = barcode;
    addTitleLookupResolvedTitle = '';
    if (finalData.box_set_proposal && displayAddBoxSetProposal(finalData.box_set_proposal, barcode)) {
      showStatus('addStatus', t('js.infoFound', movie.title), 'success');
      return;
    }
    if (finalData.metadata_candidates && finalData.metadata_candidates.length > 1) {
      _showAddCandidates(finalData.metadata_candidates);
      showStatus('addStatus', '', '');
    } else if (finalData.tmdb_candidates && finalData.tmdb_candidates.length > 1) {
      _showAddCandidates(finalData.tmdb_candidates);
      showStatus('addStatus', '', '');
    } else {
      await _doSaveManual(barcode, movie.title);
    }
  } catch(e) {
    showStatus('addStatus', t('js.connectionError', e.message), 'error');
  }
}

async function submitManual() {
  if (currentAddBoxSetProposal) {
    await saveAddBoxSetProposal();
    return;
  }
  const barcode = document.getElementById('addBarcode').value.trim();
  const title = document.getElementById('addTitle').value.trim();
  if (!barcode && !title) {
    showStatus('addStatus', t('js.barcodeOrTitleRequired'), 'error');
    return;
  }
  // Barcode is always the identifier when present. Resolve it before saving.
  if (barcode && addBarcodeLookupResolvedBarcode !== barcode) {
    await _lookupBarcodeForAdd(barcode);
    return;
  }
  // Title-only without prior metadata resolution: resolve through configured sources first.
  if (title && !barcode && !document.getElementById('addTmdbIdHidden').value.trim() && addTitleLookupResolvedTitle !== title) {
    await autoFillFromTitle();
    return;
  }
  // Both filled, or title + resolved tmdb_id: save
  const effectiveBarcode = barcode ||
    'TITLE-' + title.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 30) + '-' + Date.now().toString().slice(-6);
  await _doSaveManual(effectiveBarcode, title);
}

async function searchManualMetadata() {
  const barcode = document.getElementById('addBarcode').value.trim();
  const title = document.getElementById('addTitle').value.trim();
  if (!barcode && !title) {
    showStatus('addStatus', t('js.barcodeOrTitleRequired'), 'error');
    return;
  }
  if (barcode) {
    await _lookupBarcodeForAdd(barcode);
    return;
  }
  await autoFillFromTitle();
}

async function _doSaveManual(barcode, title) {
  const payload = {
    barcode, title,
    original_title: document.getElementById('addOriginalTitle').value,
    year:           document.getElementById('addYear').value,
    release_date:   document.getElementById('addReleaseDate').value,
    director:       document.getElementById('addDirector').value,
    actor:          document.getElementById('addActor').value,
    producer:       document.getElementById('addProducer').value,
    studios:        document.getElementById('addStudios').value,
    genre:          document.getElementById('addGenre').value,
    format:         document.getElementById('addFormat').value,
    runtime:        document.getElementById('addRuntime').value,
    rating:         document.getElementById('addRating').value,
    hdr:            document.getElementById('addHdr').value,
    language:       document.getElementById('addLanguage').value,
    audio_tracks:   document.getElementById('addAudioTracks').value,
    subtitles:      document.getElementById('addSubtitles').value,
    country:        document.getElementById('addCountry').value,
    plot:           document.getElementById('addPlot').value,
    imdb_id:        document.getElementById('addImdbId').value,
    imdb_url:       document.getElementById('addImdbUrl').value,
    tmdb_id:        document.getElementById('addTmdbIdVisible').value || document.getElementById('addTmdbIdHidden').value,
    location:       document.getElementById('addLocation').value,
    notes:          document.getElementById('addNotes').value,
    poster:         document.getElementById('addPosterHidden').value,
  };
  try {
    const r = await fetch(`${API}/movies`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (r.ok) {
      showStatus('addStatus', d.queued ? t('js.queuedAdd', title) : t('js.added', title), d.queued ? 'info' : 'success');
      clearManualForm();
      loadStats();
    } else {
      showStatus('addStatus', d.error || t('js.saveError'), 'error');
    }
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
  }
}

function clearManualForm() {
  ['addBarcode','addTitle','addOriginalTitle','addYear','addReleaseDate',
   'addDirector','addActor','addProducer','addStudios','addGenre',
   'addRuntime','addRating','addHdr','addLanguage','addAudioTracks','addSubtitles',
   'addCountry','addPlot','addImdbId','addImdbUrl','addTmdbIdVisible','addTmdbUrl',
   'addLocation','addNotes','addTmdbIdHidden','addPosterHidden'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  document.getElementById('addTmdbCandidates').style.display = 'none';
  hideAddBoxSetProposal();
  addTitleLookupResolvedTitle = '';
  addBarcodeLookupResolvedBarcode = '';
  updateAddIdentityLock();
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function showStatus(id, msg, type) {
  const el = document.getElementById(id);
  el.innerHTML = msg;
  el.className = `status-msg visible ${type}`;
}

async function parseApiJson(response) {
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (e) {
    data = null;
  }

  if (!response.ok) {
    let msg = (data && data.error) ? data.error : `HTTP ${response.status}`;
    if (!data && text) {
      const snippet = text.replace(/\s+/g, ' ').trim().slice(0, 180);
      if (snippet) msg += `: ${snippet}`;
    }
    throw new Error(msg);
  }

  if (data === null) {
    const snippet = (text || '').replace(/\s+/g, ' ').trim().slice(0, 180);
    throw new Error(`Ongeldige API-response (geen JSON): ${snippet || '(leeg)'}`);
  }

  return data;
}

// ── Movie detail swipe / keyboard navigation ──────────────────────────────────

function _updateDetailNavUI() {
  const arrows = document.getElementById('detailNavArrows');
  const idxEl  = document.getElementById('detailNavIndex');
  const prevBtn = document.getElementById('btnNavPrev');
  const nextBtn = document.getElementById('btnNavNext');
  if (!arrows) return;
  if (!_detailNavList.length) { arrows.style.display = 'none'; return; }
  const idx = _detailNavList.indexOf(currentMovieId);
  if (idx === -1) { arrows.style.display = 'none'; return; }
  arrows.style.display = 'flex';
  if (idxEl) idxEl.textContent = `${idx + 1} / ${_detailNavList.length}`;
  if (prevBtn) prevBtn.disabled = idx === 0;
  if (nextBtn) nextBtn.disabled = idx === _detailNavList.length - 1;
}

function navigateDetailMovie(direction) {
  if (!_detailNavList.length) return;
  const idx = _detailNavList.indexOf(currentMovieId);
  if (idx === -1) return;
  const newIdx = idx + direction;
  if (newIdx < 0 || newIdx >= _detailNavList.length) return;
  openMovieDetail(_detailNavList[newIdx]);
}

function initDetailSwipe() {
  const panel = document.getElementById('panel-movie-detail');
  if (!panel) return;

  let _touchStartX = 0;
  let _touchStartY = 0;

  panel.addEventListener('touchstart', e => {
    _touchStartX = e.touches[0].clientX;
    _touchStartY = e.touches[0].clientY;
  }, { passive: true });

  panel.addEventListener('touchend', e => {
    const dx = e.changedTouches[0].clientX - _touchStartX;
    const dy = e.changedTouches[0].clientY - _touchStartY;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy)) return;
    navigateDetailMovie(dx < 0 ? 1 : -1);
  }, { passive: true });

  // Keyboard navigation (arrow keys) when detail panel is active
  document.addEventListener('keydown', e => {
    const panel = document.getElementById('panel-movie-detail');
    if (!panel || !panel.classList.contains('active')) return;
    // Don't interfere with input fields
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.key === 'ArrowLeft')  navigateDetailMovie(-1);
    if (e.key === 'ArrowRight') navigateDetailMovie(1);
  });
}

document.addEventListener('DOMContentLoaded', initDetailSwipe);

// ── Edition helpers ───────────────────────────────────────────────────────────

function _editionShortLabel(edType, customLabel) {
  if (edType === 'custom') return customLabel || '…';
  const labels = {
    steelbook:    'Steelbook',
    directors_cut: "Director's Cut",
    limited:      'Limited Edition',
    theatrical:   'Theatrical Cut',
    '4k_upgrade': '4K',
    '4k_combo':   '4K Ultra HD + Blu-ray',
    boxset_disc:  'Box-Set',
    dvd:          'DVD',
    bluray:       'Blu-ray',
  };
  return labels[edType] || edType;
}

function switchEditTab(name) {
  console.log('[DV] switchEditTab called:', name);
  document.querySelectorAll('[data-edit-tab]').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`[data-edit-tab="${name}"]`);
  if (btn) btn.classList.add('active');
  const tabs = { General: 'editTabGeneral', Edition: 'editTabEdition', Details: 'editTabDetails' };
  Object.entries(tabs).forEach(([key, id]) => {
    const el = document.getElementById(id);
    console.log('[DV]  ', id, 'found:', !!el, 'setting:', key.toLowerCase() === name ? 'grid' : 'none');
    if (el) el.style.display = (key.toLowerCase() === name) ? 'grid' : 'none';
  });
}

function setEditionFilter(btn) {
  activeEditionFilter = !activeEditionFilter;
  btn.classList.toggle('active', activeEditionFilter);
  filterMovies();
}

function _stackBadgeLabel(m) {
  const countPrefix = m.editions_count + '\u00d7 ';
  if (!m.editions) return countPrefix;
  const fmtShort = { '4K UHD': '4K', 'Blu-ray': 'BD', 'DVD': 'DVD' };
  const lbls = [...new Set(m.editions.map(e => {
    if (e.edition_type && e.edition_type !== 'standard')
      return e.edition_type === 'custom' ? (e.custom_edition_label || '\u2026') : _editionShortLabel(e.edition_type);
    return fmtShort[e.format] || (e.format || '?').slice(0, 3);
  }))];
  if (lbls.length <= 3) return countPrefix + lbls.join('\u00b7');
  return countPrefix + lbls.slice(0, 2).join('\u00b7') + `+${lbls.length - 2}`;
}

function toggleCustomEditionInput() {
  const sel = document.getElementById('editEditionType');
  const inp = document.getElementById('editCustomEditionLabel');
  if (inp) inp.style.display = (sel && sel.value === 'custom') ? '' : 'none';
}

function toggleEditionsDrawer(id) {
  const drawer = document.getElementById(`edDrawer_${id}`);
  if (!drawer) return;
  // Close if already open
  if (drawer.style.display !== 'none' && drawer.innerHTML) {
    drawer.style.display = 'none';
    return;
  }
  const movie = allMovies.find(m => m.id === id);
  if (!movie || !movie.editions) return;
  // Make all editions accessible via openMovieDetail
  movie.editions.forEach(e => {
    if (!allMovies.some(m => m.id === e.id)) {
      allMovies.push({ ...e, _isNested: true, _primaryId: id });
    }
  });
  // Render mini-cards for each edition
  drawer.innerHTML = movie.editions.map(e => {
    const fmt = e.format || '4K';
    const edLabel = (e.edition_type && e.edition_type !== 'standard')
      ? `<span style="color:var(--accent);font-size:0.72rem;">${_editionShortLabel(e.edition_type)}</span>`
      : '';
    const isPrimary = (e.id === id) ? `<span style="color:var(--text-muted);font-size:0.68rem;">★</span>` : '';
    return `<div onclick="event.stopPropagation(); openMovieDetail(${e.id})"
               style="padding:6px 10px; display:flex; align-items:center; gap:8px; cursor:pointer; border-top:1px solid rgba(255,255,255,.06); font-size:0.8rem; color:var(--text);">
      <span style="background:rgba(232,197,71,.15); color:var(--accent); border-radius:4px; padding:1px 5px; font-size:0.71rem; font-weight:700;">${fmt}</span>
      ${edLabel}${isPrimary}
    </div>`;
  }).join('');
  drawer.style.display = 'block';
}

function _isBoxSetGroup(g) {
  return g && (g.group_type === 'boxset' || (g.child_group_count || 0) > 0 || (g.loose_movie_count || 0) > 0);
}

// ── Group Management (admin panel) ───────────────────────────────────────────

let _gmFilter = 'all';

async function loadGroupMgmtList(filter) {
  _gmFilter = filter || 'all';
  // Update filter button states
  ['all','vault','boxset','collection'].forEach(f => {
    const btn = document.getElementById('gmFilter' + f.charAt(0).toUpperCase() + f.slice(1));
    if (btn) btn.classList.toggle('active', f === _gmFilter);
  });

  const container = document.getElementById('groupMgmtList');
  if (!container) return;
  container.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('general.loading', 'Laden...')}</div>`;

  try {
    const items = [];

    // Fetch edition groups (Vaults + Box Sets)
    if (_gmFilter === 'all' || _gmFilter === 'vault' || _gmFilter === 'boxset') {
      const r = await fetch(`${API}/edition-groups`);
      const egs = await r.json();
      for (const eg of egs) {
        // Box Set = has child edition_groups OR has loose movies via super_group_id
        const hasChildGroups = (eg.child_group_count || 0) > 0;
        const hasLooseMovies = (eg.loose_movie_count || 0) > 0;
        const isChildOfBoxSet = eg.parent_group_id != null;
        let type;
        if (eg.group_type === 'boxset' || hasChildGroups || hasLooseMovies) type = 'boxset';
        else if (isChildOfBoxSet) type = 'vault'; // child vault inside a box set
        else type = 'vault'; // standalone vault

        const totalMembers = (eg.member_count || 0) + (eg.loose_movie_count || 0) + (eg.child_member_count || 0);
        if (_gmFilter !== 'all' && _gmFilter !== type) continue;
        items.push({ id: eg.id, title: eg.title, barcode: eg.barcode || '', type, memberCount: totalMembers, src: 'eg' });
      }
    }

    // Fetch collections
    if (_gmFilter === 'all' || _gmFilter === 'collection') {
      const r = await fetch(`${API}/collections`);
      const cols = await r.json();
      for (const c of cols) {
        const totalMembers = (c.eg_movie_count || 0) + (c.loose_movie_count || 0) + (c.boxset_loose_count || 0);
        items.push({ id: c.id, title: c.title, type: 'collection', memberCount: totalMembers, src: 'col' });
      }
    }

    if (items.length === 0) {
      container.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">${t('settings.groupMgmtEmpty', 'Geen groepen gevonden.')}</div>`;
      return;
    }

    items.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
    const canManage = !authEnabled || currentUserRole === 'admin';
    container.innerHTML = items.map(item => {
      const typeBadge = item.type === 'collection' ? 'Collection'
        : item.type === 'boxset' ? 'Box Set' : 'Vault';
      const badgeColor = item.type === 'collection' ? '#2ecc71'
        : item.type === 'boxset' ? 'var(--accent2)' : 'var(--accent)';
      return `
        <div class="group-mgmt-item">
          <span style="font-size:0.72rem; padding:2px 8px; border-radius:4px; background:${badgeColor}22; color:${badgeColor}; font-weight:600; white-space:nowrap;">${typeBadge}</span>
          ${canManage && item.src === 'eg' ? `<div class="group-type-control" role="group" aria-label="Containertype wijzigen">
            <button type="button" class="${item.type === 'vault' ? 'active' : ''}" onclick="changeGroupMgmtType(${item.id}, 'vault')" title="Wijzig naar Vault">Vault</button>
            <button type="button" class="${item.type === 'boxset' ? 'active' : ''}" onclick="changeGroupMgmtType(${item.id}, 'boxset')" title="Wijzig naar Box Set">Box Set</button>
          </div>` : ''}
          <input type="text" value="${(item.title || '').replace(/"/g, '&quot;')}" style="flex:1; font-size:0.85rem; background:transparent; border:1px solid transparent; padding:4px 8px; border-radius:4px; color:var(--text);"
                 ${canManage ? `onfocus="this.style.borderColor='var(--accent)'" onblur="this.style.borderColor='transparent'" onchange="renameGroupMgmt('${item.src}', ${item.id}, this.value)"` : 'readonly'}
          >
          ${item.src === 'eg' ? `<input type="text" value="${escHtml(item.barcode || '')}" placeholder="EAN" inputmode="numeric" aria-label="EAN barcode"
                 style="width:150px; max-width:18vw; font-size:0.78rem; font-family:'DM Mono',monospace; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); padding:4px 8px; border-radius:4px; color:var(--text);"
                 ${canManage ? `onfocus="this.style.borderColor='var(--accent)'" onblur="this.style.borderColor='rgba(255,255,255,0.08)'" onchange="updateGroupMgmtBarcode(${item.id}, this.value)"` : 'readonly'}
          >` : ''}
          <span style="font-size:0.75rem; color:var(--text-muted); white-space:nowrap;">${item.memberCount} film${item.memberCount !== 1 ? 's' : ''}</span>
          ${canManage ? `<button type="button" onclick="deleteGroupMgmt('${item.src}', ${item.id}, '${(item.title || '').replace(/'/g, "\\'")}')"
                  style="background:none; border:none; cursor:pointer; color:var(--danger); font-size:1rem; padding:4px;" title="Verwijderen">🗑</button>` : ''}
        </div>`;
    }).join('');
  } catch (e) {
    container.innerHTML = `<div style="color:var(--danger); font-size:0.85rem;">Error: ${e.message}</div>`;
  }
}

async function renameGroupMgmt(src, id, newTitle) {
  const url = src === 'col' ? `${API}/collections/${id}` : `${API}/edition-groups/${id}`;
  await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: newTitle })
  });
}

async function changeGroupMgmtType(id, groupType) {
  await fetch(`${API}/edition-groups/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group_type: groupType === 'boxset' ? 'boxset' : 'vault' })
  });
  await loadCollection();
  loadGroupMgmtList(_gmFilter);
}

async function updateGroupMgmtBarcode(id, barcode) {
  await fetch(`${API}/edition-groups/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ barcode: (barcode || '').trim() })
  });
}

async function deleteGroupMgmt(src, id, title) {
  if (!confirm(`${title} verwijderen?`)) return;
  const url = src === 'col' ? `${API}/collections/${id}` : `${API}/edition-groups/${id}`;
  await fetch(url, { method: 'DELETE' });
  loadGroupMgmtList(_gmFilter);
}

// ── Compare mode ─────────────────────────────────────────────────────────────

async function toggleCompareMode() {
  const btn   = document.getElementById('btnCompareMode');
  const grid  = document.getElementById('moviesGrid');
  const view  = document.getElementById('compareView');
  const count = document.getElementById('filterCount');

  if (compareMode) {
    // Exit compare mode
    compareMode = false;
    btn.classList.remove('btn-primary');
    btn.classList.add('btn-secondary');
    if (grid)  grid.style.display = '';
    if (view)  view.style.display = 'none';
    if (count) count.style.display = '';
    filterMovies();
    return;
  }

  // Enter compare mode
  compareMode = true;
  btn.classList.add('btn-primary');
  btn.classList.remove('btn-secondary');
  if (grid)  grid.style.display = 'none';
  if (view)  view.style.display = 'block';
  if (count) count.style.display = 'none';

  // Show loading
  const both = document.getElementById('compareContentBoth');
  if (both) both.innerHTML = `<div style="grid-column:1/-1; color:var(--text-muted); padding:20px;" data-i18n="general.loading">${t('general.loading')}</div>`;

  try {
    const r = await fetch(`${API}/collection/compare`);
    compareData = await r.json();
    // Also populate digital badges for normal grid
    renderCompareTab(activeCompareTab);
  } catch(e) {
    if (both) both.innerHTML = `<div style="grid-column:1/-1; color:var(--danger); padding:20px;">${t('js.error', e.message)}</div>`;
  }
}

function switchCompareTab(tab) {
  activeCompareTab = tab;
  ['both', 'physical', 'digital'].forEach(t2 => {
    const btn  = document.getElementById(`compareTab${t2.charAt(0).toUpperCase() + t2.slice(1)}`);
    const cont = document.getElementById(`compareContent${t2.charAt(0).toUpperCase() + t2.slice(1)}`);
    if (btn)  btn.classList.toggle('active', t2 === tab);
    if (cont) cont.style.display = t2 === tab ? '' : 'none';
  });
  renderCompareTab(tab);
}

function renderCompareTab(tab) {
  if (!compareData) return;
  if (tab === 'both') {
    const cont = document.getElementById('compareContentBoth');
    const items = compareData.physical_and_digital || [];
    if (!cont) return;
    if (!items.length) {
      cont.innerHTML = `<div class="empty-state" style="grid-column:1/-1"><span class="big-icon">💿</span><h3>${t('compare.noBoth')}</h3></div>`;
      return;
    }
    cont.innerHTML = items.map(entry => {
      const m   = entry.movie;
      const src = posterSrc(m);
      const img = src ? `<img src="${src}" loading="lazy">` : '<div class="no-img">🎬</div>';
      const sources = _digitalMatches(entry).map(x =>
        `<span style="font-size:0.72rem; background:rgba(${x.sourceType==='plex'?'232,197,71':'124,106,247'},.15); color:var(--${x.sourceType==='plex'?'accent':'accent2'}); border:1px solid rgba(${x.sourceType==='plex'?'232,197,71':'124,106,247'},.3); border-radius:4px; padding:1px 6px;">${x.sourceName}</span>`
      ).join(' ');
      return `<div class="movie-card" data-id="${m.id}" onclick="openMovieDetail(${m.id})">
        <div class="movie-card-poster">${img}<div class="movie-card-format">${m.format||'4K'}</div></div>
        <div class="movie-card-info"><div class="movie-card-title">${m.title}</div>
        <div class="movie-card-year" style="display:flex;flex-wrap:wrap;gap:3px;margin-top:3px;">${sources}</div></div></div>`;
    }).join('');
  } else if (tab === 'physical') {
    const cont = document.getElementById('compareContentPhys');
    const items = compareData.physical_only || [];
    if (!cont) return;
    if (!items.length) {
      cont.innerHTML = `<div class="empty-state" style="grid-column:1/-1"><span class="big-icon">✅</span><h3>${t('compare.allRipped')}</h3></div>`;
      return;
    }
    cont.innerHTML = items.map(entry => {
      const m   = entry.movie;
      const src = posterSrc(m);
      const img = src ? `<img src="${src}" loading="lazy">` : '<div class="no-img">🎬</div>';
      return `<div class="movie-card" data-id="${m.id}" onclick="openMovieDetail(${m.id})">
        <div class="movie-card-poster">${img}<div class="movie-card-format">${m.format||'4K'}</div></div>
        <div class="movie-card-info"><div class="movie-card-title">${m.title}</div>
        <div class="movie-card-year">${m.year||'—'}</div></div></div>`;
    }).join('');
  } else {
    const cont = document.getElementById('compareContentDigital');
    const items = compareData.digital_only || [];
    if (!cont) return;
    if (!items.length) {
      cont.innerHTML = `<div class="empty-state"><span class="big-icon">📺</span><h3>${t('compare.noDigitalOnly')}</h3></div>`;
      return;
    }
    cont.innerHTML = `<div style="display:flex; flex-direction:column; gap:8px;">` +
      items.map(item => {
        const sourceType = item.sourceType || item.source_type || '';
        const sourceName = item.sourceName || item.source_name || '';
        return `<div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:8px;">
          <div style="flex:1; min-width:0;">
            <div style="font-weight:500; font-size:0.9rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${item.title}</div>
            <div style="font-size:0.78rem; color:var(--text-muted);">${item.year||'—'}</div>
          </div>
          <span style="font-size:0.72rem; background:rgba(${sourceType==='plex'?'232,197,71':'124,106,247'},.15); color:var(--${sourceType==='plex'?'accent':'accent2'}); border:1px solid rgba(${sourceType==='plex'?'232,197,71':'124,106,247'},.3); border-radius:4px; padding:2px 8px; flex-shrink:0;">${sourceName}</span>
        </div>`;
      }).join('') + `</div>`;
  }
}
