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
let groupEditionsEnabled = localStorage.getItem('dv_group_editions') === 'true';
let showDigitalBadges    = localStorage.getItem('dv_digital_badges') === 'true';
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
    const matchesEdition = !activeEditionFilter || (m.edition_group_id != null);
    const matchesQuery   = !q ||
      (m.title || '').toLowerCase().includes(q) ||
      (m.original_title || '').toLowerCase().includes(q) ||
      (m.director || '').toLowerCase().includes(q) ||
      (m.actor || '').toLowerCase().includes(q) ||
      (m.genre || '').toLowerCase().includes(q) ||
      (m.box_set || '').toLowerCase().includes(q) ||
      (m.studios || '').toLowerCase().includes(q) ||
      (m.distributor || '').toLowerCase().includes(q);
    return matchesFormat && matchesGroup && matchesEdition && matchesQuery;
  });
}

function canEditMovie(m) {
  return !authEnabled || !currentUserId || currentUserRole === 'admin' || String(m.owner_id) === String(currentUserId);
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
    const displayTitle = isGroupCard ? (m._group_title || m.title) : m.title;
    const displayYear  = isGroupCard ? `${m.editions_count} edities` : (m.year || '\u2014');

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
      ? `<div class="movie-card-group-indicator" title="Onderdeel van een editiegroep">🗂</div>`
      : '';

    // Format label: for group cards show edition count, otherwise show format
    const formatLabel = isGroupCard
      ? `<div class="movie-card-format group-count-badge">${m.editions_count}×</div>`
      : `<div class="movie-card-format">${m.format || '4K'}</div>`;

    // Digital badge (Plex/Jellyfin)
    let digitalBadge = '';
    if (showDigitalBadges && compareData) {
      const match = (compareData.physical_and_digital || []).find(e => e.movie && e.movie.id === m.id);
      if (match && match.digital_matches && match.digital_matches.length) {
        const srcType = match.digital_matches[0].source_type;
        digitalBadge = `<div class="movie-card-digital-badge" title="${match.digital_matches.map(x=>x.source_name).join(', ')}">${srcType === 'plex' ? '🟡' : '🔵'}</div>`;
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
  return allMovies.filter(m =>
    (m.title || '').toLowerCase().includes(q) ||
    (m.original_title || '').toLowerCase().includes(q) ||
    (m.director || '').toLowerCase().includes(q) ||
    (m.actor || '').toLowerCase().includes(q) ||
    (m.genre || '').toLowerCase().includes(q) ||
    (m.box_set || '').toLowerCase().includes(q) ||
    (m.studios || '').toLowerCase().includes(q) ||
    (m.distributor || '').toLowerCase().includes(q)
  );
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

async function bulkDelete() {
  const ids = [...selectedIds];
  if (!ids.length) return;
  if (!confirm(t('js.confirmBulkDelete', ids.length))) return;

  showProgress(t('js.deleting'), ids.length);
  try {
    const r = await fetch(`${API}/movies/bulk-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids })
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
let _egViewStack = []; // navigation stack for nested group views
let _currentSuperGroup = null;

function openSuperGroupView(movie) {
  _currentSuperGroup = movie;

  // Inject sub-group cards into allMovies so openEditionGroupView can find them
  (movie._sub_groups || []).forEach(child => {
    if (!allMovies.some(m => m.id === child.id))
      allMovies.push({ ...child, _isNested: true });
  });
  (movie._loose_movies || []).forEach(lm => {
    if (!allMovies.some(m => m.id === lm.id))
      allMovies.push({ ...lm, _isNested: true });
  });

  document.getElementById('egPanelTitle').textContent = movie._group_title || movie.title || '';
  const subCnt  = movie._sub_group_count || 0;
  const looseCnt = (movie._loose_movies || []).length;
  const totalEditions = movie.editions_count || 0;
  document.getElementById('egPanelSubtitle').textContent =
    [subCnt ? `${subCnt} set${subCnt !== 1 ? 's' : ''}` : null,
     looseCnt ? `${looseCnt} losse film${looseCnt !== 1 ? 's' : ''}` : null,
     `${totalEditions} edities`].filter(Boolean).join(' \u00b7 ');
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
  const looseHtml = (movie._loose_movies || []).map(lm => {
    const src = posterSrc(lm);
    const imgHtml = src
      ? `<img src="${src}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>\ud83c\udfa6</div>'">`
      : '<div class="no-img">\ud83c\udfa6</div>';
    const edType = lm.edition_type && lm.edition_type !== 'standard'
      ? _editionShortLabel(lm.edition_type, lm.custom_edition_label) : '';
    return `
      <div class="eg-edition-card" onclick="openMovieDetail(${lm.id})">
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
  window.scrollTo({ top: 0, behavior: 'smooth' });
  switchTabDirect('edition-group');
}

function openEditionGroupViewFromSuper(id) {
  _egViewStack.push({ type: 'super', movie: _currentSuperGroup });
  openEditionGroupView(id);
}

function openEditionGroupView(id) {
  _currentEditionGroupPrimaryId = id;
  const primary = allMovies.find(m => m.id === id);
  if (!primary) return;
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
    `${(primary.editions || []).length} edities${editionTypes.length ? ' · ' + editionTypes.join(' · ') : ''}`;
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
      <div class="eg-edition-card" onclick="openMovieDetail(${e.id})">
        <div class="eg-edition-poster">
          ${imgHtml}
          <div class="eg-edition-fmt">${e.format || '4K'}</div>
          ${typeLabel ? `<div class="eg-edition-type-label">${typeLabel}</div>` : ''}
          ${isPrimary ? '<div class="eg-primary-marker">★</div>' : ''}
        </div>
        <div class="eg-edition-info">${e.year || ''}</div>
      </div>`;
  }).join('');
  window.scrollTo({ top: 0, behavior: 'smooth' });
  _populateEgManageSection(primary);
  switchTabDirect('edition-group');
}

function _populateEgManageSection(movieCard) {
  const bl   = movieCard && movieCard._group_badge_label;
  const elBl = document.getElementById('egBadgeLabel');
  if (elBl) elBl.value = bl || '';
  const titleEl = document.getElementById('egGroupTitle');
  if (titleEl) titleEl.value = (movieCard && movieCard._group_title) || '';
  const elPgId   = document.getElementById('egParentGroupId');
  const badge    = document.getElementById('egParentGroupBadge');
  const pgSearch = document.getElementById('egParentGroupSearch');
  const manage   = document.getElementById('egManageSection');
  if (manage) manage.style.display = 'none';
  if (pgSearch) pgSearch.value = '';

  const existingParentId = movieCard && movieCard._parent_group_id;
  if (elPgId) elPgId.value = existingParentId || '';

  if (existingParentId && badge) {
    // Load parent name from API
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
}

function toggleEgManageSection() {
  const s = document.getElementById('egManageSection');
  if (s) s.style.display = (s.style.display === 'none') ? '' : 'none';
}

function searchParentGroup(query) {
  const dropdown = document.getElementById('egParentGroupDropdown');
  if (!dropdown) return;
  if (!query || query.length < 1) { dropdown.style.display = 'none'; return; }
  fetch(`${API}/edition-groups?q=${encodeURIComponent(query)}`)
    .then(r => r.json())
    .then(groups => {
      const items = groups.slice(0, 8).map(g =>
        `<div style="padding:8px 12px; cursor:pointer; font-size:0.85rem;" onclick="selectParentGroup(${g.id}, '${(g.title||'').replace(/'/g,"\\'")}')">${g.title} <span style='color:var(--text-muted);font-size:0.75rem;'>(${g.member_count || 0} edities)</span></div>`
      );
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
    body: JSON.stringify({ title })
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
  const primary = allMovies.find(m => m.id === _currentEditionGroupPrimaryId);
  const groupId = primary && primary.edition_group_id;
  if (!groupId) return;
  const title         = (document.getElementById('egGroupTitle') || {}).value || null;
  const badge_label   = (document.getElementById('egBadgeLabel') || {}).value || null;
  const parent_raw    = (document.getElementById('egParentGroupId') || {}).value;
  const parent_group_id = parent_raw ? parseInt(parent_raw) : null;
  await fetch(`${API}/edition-groups/${groupId}`, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ title, badge_label, parent_group_id })
  });
  _editionGroupCache = [];
  await loadCollection();
  closeEditionGroupView();
}

async function deleteEditionGroup() {
  const primary = allMovies.find(m => m.id === _currentEditionGroupPrimaryId);
  const groupId = primary && primary.edition_group_id;
  if (!groupId) return;
  const name = (document.getElementById('egGroupTitle') || {}).value || (primary && primary._group_title) || '';
  if (!confirm(t('js.egDeleteConfirm', name))) return;
  await fetch(`${API}/edition-groups/${groupId}`, { method: 'DELETE' });
  _editionGroupCache = [];
  await loadCollection();
  closeEditionGroupView();
}

function closeEditionGroupView() {
  if (_egViewStack.length > 0) {
    const prev = _egViewStack.pop();
    if (prev.type === 'super') { openSuperGroupView(prev.movie); return; }
  }
  _replaceRoute(_tabPath(_detailReturnTab));
  switchTabDirect(_detailReturnTab);
}

async function openMovieDetail(id) {
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
  const movie = allMovies.find(m => m.id === id);
  if (!movie) return;

  // Redirect grouped editions to the stack view when group_editions mode is active
  if (groupEditionsEnabled && (movie.editions_count || 0) > 1 && !movie._isNested) {
    if (movie._is_super_group) {
      openSuperGroupView(movie);
    } else {
      openEditionGroupView(id);
    }
    return;
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

  const src = posterSrc(movie);
  const poster = document.getElementById('modalPoster');
  poster.innerHTML = src
    ? `<img src="${src}" onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>🎬</div>'">`
    : '<div class="no-img">🎬</div>';

  const bg   = document.getElementById('movieDetailBg');
  const hero = document.getElementById('detailHeroImg');
  const heroWrap = document.querySelector('.detail-hero-wrap');
  const backdropUrl = movie.backdrop || '';
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

  d.innerHTML = [
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
    _replaceRoute('/');
  } else {
    _replaceRoute(_tabPath(_detailReturnTab));
    switchTabDirect(_detailReturnTab);
  }
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
  const tabMap = { info: 'detailTabInfo', cast: 'detailTabCast', media: 'detailTabMedia' };
  const tabContent = document.getElementById(tabMap[tab] || 'detailTabInfo');
  if (tabContent) tabContent.classList.add('active');
  if (tab === 'cast' && !_castLoaded) {
    _castLoaded = true;
    loadMovieCast(currentMovieId);
  }
  if (tab === 'media' && !_mediaLoaded) {
    _mediaLoaded = true;
    loadMovieMedia();
  }
}

function _playYouTube(key) {
  const el = document.getElementById(`ytThumb_${key}`);
  if (!el) return;
  el.innerHTML = `<iframe style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;" src="https://www.youtube-nocookie.com/embed/${key}?autoplay=1" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen title="Trailer"></iframe>`;
}

function loadMovieMedia() {
  const container = document.getElementById('mediaTabContent');
  if (!container) return;
  const movie = allMovies.find(m => m.id === currentMovieId) || {};
  let html = '';

  // Trailer
  const trailerUrl = movie.trailer_url || '';
  const ytMatch = trailerUrl.match(/[?&]v=([^&]+)/) || trailerUrl.match(/youtu\.be\/([^?&]+)/);
  if (ytMatch) {
    const ytKey = ytMatch[1];
    html += `<div style="margin-bottom:24px;">
      <div style="font-size:0.78rem;font-weight:700;color:var(--text-muted);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;">${t('modal.trailer')}</div>
      <div id="ytThumb_${ytKey}" onclick="_playYouTube('${ytKey}')" style="position:relative;width:100%;padding-bottom:56.25%;border-radius:10px;overflow:hidden;background:#111;cursor:pointer;">
        <img src="https://img.youtube.com/vi/${ytKey}/maxresdefault.jpg" onerror="this.onerror=null;this.src='https://img.youtube.com/vi/${ytKey}/hqdefault.jpg'" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;" alt="">
        <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:64px;height:46px;background:rgba(180,0,0,0.88);border-radius:10px;display:flex;align-items:center;justify-content:center;">
          <span style="display:block;width:0;height:0;border-style:solid;border-width:14px 0 14px 26px;border-color:transparent transparent transparent #fff;margin-left:4px;"></span>
        </div>
      </div>
    </div>`;
  }

  // Backdrops
  let backdrops = [];
  try { backdrops = movie.backdrops ? JSON.parse(movie.backdrops) : []; } catch(e) {}
  if (!backdrops.length && movie.backdrop) backdrops = [movie.backdrop];
  if (backdrops.length) {
    html += `<div style="font-size:0.78rem;font-weight:700;color:var(--text-muted);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;">${t('modal.backdrops')} (${backdrops.length})</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px;">
      ${backdrops.map(url => `<a href="${url}" target="_blank" rel="noopener noreferrer" style="display:block;border-radius:8px;overflow:hidden;">
        <img src="${url}" loading="lazy" style="width:100%;display:block;aspect-ratio:16/9;object-fit:cover;transition:transform .2s;" onmouseover="this.style.transform='scale(1.03)'" onmouseout="this.style.transform='scale(1)'">
      </a>`).join('')}
    </div>`;
  }

  if (!html) {
    html = `<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:0.88rem;">${t('modal.noMedia')}</div>`;
  }
  container.innerHTML = html;
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
        const photoSrc = a.photo_url || (a.photo_file ? `${API}/profiles/${a.photo_file}?v=${encodeURIComponent(a.photo_file)}` : '');
        const photo = a.photo_file
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
        const photoSrc = c.photo_url || (c.photo_file ? `${API}/profiles/${c.photo_file}?v=${encodeURIComponent(c.photo_file)}` : '');
        const photo = c.photo_file
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
    if (p.photo_file) {
      const photoSrc = p.photo_url || `${API}/profiles/${p.photo_file}?v=${encodeURIComponent(p.photo_file)}`;
      document.getElementById('personPhoto').innerHTML =
        `<img class="person-photo-large" src="${photoSrc}" onerror="this.outerHTML='<div class=\\'person-photo-placeholder-large\\'>👤</div>'">`; 
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
    // Filmography from collection
    const movies = p.movies || [];
    if (!movies.length) {
      document.getElementById('personFilmography').innerHTML =
        `<div style="color:var(--text-muted); font-size:0.85rem;">${t('js.noMoviesFound')}</div>`;
      return;
    }
    let filmHtml = '';
    movies.forEach(m => {
      const src = m.poster_file ? `${API}/posters/${m.poster_file}` : (m.poster || '');
      const posterImg = src
        ? `<img class="person-film-poster" src="${src}" onerror="this.style.display='none'">`
        : `<div class="person-film-poster" style="display:flex;align-items:center;justify-content:center;font-size:1.5rem;color:var(--text-muted)">🎬</div>`;
      const role = m.character ? `<div class="person-film-year">${t('person.as')} ${escHtml(m.character)}</div>`
                 : m.job ? `<div class="person-film-year">${escHtml(m.job)}</div>` : '';
      filmHtml += `<div class="person-film-card" onclick="_detailReturnTab='person-detail';openMovieDetail(${m.id})">`;
      filmHtml += `${posterImg}<div class="person-film-title">${escHtml(m.title)}</div><div class="person-film-year">${m.year || ''} · ${m.format || ''}</div>${role}</div>`;
    });
    document.getElementById('personFilmography').innerHTML = filmHtml;
  } catch(e) {
    document.getElementById('personFilmography').innerHTML =
      `<div style="color:var(--danger)">${t('js.error', e.message)}</div>`;
  }
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

function startEdit() {
  const movie = allMovies.find(m => m.id === currentMovieId);
  if (!movie) return;

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

  // Populate edition group field
  const egId = movie.edition_group_id;
  document.getElementById('editEditionGroupId').value = egId || '';
  document.getElementById('editEditionGroupSearch').value = '';
  const egDropdown = document.getElementById('editEditionGroupDropdown');
  if (egDropdown) egDropdown.style.display = 'none';
  const badge = document.getElementById('editEditionGroupBadge');
  if (egId) {
    // Look up group name from cache or fetch
    const cached = _editionGroupCache.find(g => g.id === egId);
    if (cached) {
      document.getElementById('editEditionGroupName').textContent = cached.title;
      badge.style.display = 'flex';
    } else {
      const _editingId = currentMovieId;
      fetch(`${API}/edition-groups/${egId}`).then(r => r.json()).then(g => {
        if (currentMovieId !== _editingId) return; // stale — user already opened another movie
        document.getElementById('editEditionGroupName').textContent = g.title || '';
        badge.style.display = 'flex';
        _editionGroupCache.push(g); // cache it so next open is instant
      }).catch(() => {});
    }
  } else {
    badge.style.display = 'none';
  }

  // Populate super-group field
  const sgId = movie.super_group_id;
  const sgIdEl = document.getElementById('editSuperGroupId');
  const sgSearch = document.getElementById('editSuperGroupSearch');
  const sgBadge = document.getElementById('editSuperGroupBadge');
  const sgDropdown = document.getElementById('editSuperGroupDropdown');
  if (sgIdEl) sgIdEl.value = sgId || '';
  if (sgSearch) sgSearch.value = '';
  if (sgDropdown) sgDropdown.style.display = 'none';
  if (sgId && sgBadge) {
    fetch(`${API}/edition-groups/${sgId}`).then(r => r.ok ? r.json() : null).then(g => {
      if (!g) return;
      document.getElementById('editSuperGroupName').textContent = g.title || '';
      sgBadge.style.display = 'flex';
    }).catch(() => {});
  } else if (sgBadge) {
    sgBadge.style.display = 'none';
  }

  document.getElementById('editStatus').className = 'status-msg';
  switchEditTab('general'); // initialize tab state before showing modal
  document.getElementById('modalViewMode').style.display = 'none';
  document.getElementById('modalEditMode').style.display = 'block';
  toggleCustomEditionInput(); // Show/hide custom label input based on current edition type

  // Snapshot original values for dirty-checking
  _editSnapshot = {};
  for (const [suffix] of Object.entries(EDIT_FIELDS)) {
    const el = document.getElementById('edit' + suffix);
    if (el) _editSnapshot[suffix] = el.value;
  }
  _editSnapshot._groups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
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
  // Edition group assignment
  const egIdEl = document.getElementById('editEditionGroupId');
  if (egIdEl) {
    const egVal = egIdEl.value.trim();
    payload.edition_group_id = egVal ? parseInt(egVal) : null;
  }
  // Super-group assignment
  const sgIdEl = document.getElementById('editSuperGroupId');
  if (sgIdEl) {
    const sgVal = sgIdEl.value.trim();
    payload.super_group_id = sgVal ? parseInt(sgVal) : null;
  }

  // Collect selected group IDs from checkboxes
  const selectedGroupIds = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => parseInt(cb.value));
  // Capture previous edition group for comparison after save
  const _prevEgId = (allMovies.find(m => m.id === currentMovieId) || {}).edition_group_id ?? null;

  try {
    const r = await fetch(`${API}/movies/${currentMovieId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (r.ok) {
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
        if (idx >= 0) allMovies[idx] = { ...allMovies[idx], ...payload, group_ids: selectedGroupIds };
        filterMovies();
        // Reset dirty state so cancel/back doesn't prompt about unsaved changes
        _editSnapshot = {};
        for (const [suffix] of Object.entries(EDIT_FIELDS)) {
          const el = document.getElementById('edit' + suffix);
          if (el) _editSnapshot[suffix] = el.value;
        }
        _editSnapshot._groups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
        _editDirty = false;
      } else {
        // Update local cache
        const idx = allMovies.findIndex(m => m.id === currentMovieId);
        if (idx >= 0) allMovies[idx] = { ...allMovies[idx], ...d };

        showStatus('editStatus', t('js.saved'), 'success');
        // Stay in edit mode — update snapshot so fields are no longer dirty
        _editSnapshot = {};
        for (const [suffix] of Object.entries(EDIT_FIELDS)) {
          const el = document.getElementById('edit' + suffix);
          if (el) _editSnapshot[suffix] = el.value;
        }
        _editSnapshot._groups = [...document.querySelectorAll('.edit-group-cb:checked')].map(cb => cb.value).sort().join(',');
        _editDirty = false;
        _editionGroupCache = []; // Invalidate so next search fetches fresh member counts
        // Reload full collection when edition group assignment changes in grouped view
        if (groupEditionsEnabled && (payload.edition_group_id ?? null) !== _prevEgId) {
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
async function autoFillFromTitle() {
  const title = document.getElementById('addTitle').value.trim();
  if (!title) return;
  showStatus('addStatus', t('js.searchingMovie'), 'info');
  document.getElementById('addTmdbCandidates').style.display = 'none';
  try {
    const r = await fetch(`${API}/tmdb_candidates?title=${encodeURIComponent(title)}`);
    const d = await r.json();
    const cands = d.candidates || [];
    if (cands.length === 0) {
      showStatus('addStatus', t('js.noInfoFound'), 'error');
    } else if (cands.length === 1) {
      await _fillAddFormFromTmdbId(cands[0].tmdb_id);
    } else {
      _showAddCandidates(cands);
      showStatus('addStatus', '', '');
    }
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
  }
}

function _showAddCandidates(candidates) {
  const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
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

async function _selectAddCandidate(tmdbId) {
  document.getElementById('addTmdbCandidates').style.display = 'none';
  const existingBarcode = document.getElementById('addBarcode').value.trim();
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
      // Barcode was already entered: auto-save directly
      await _doSaveManual(existingBarcode, d.movie.title);
    } else {
      // Title-only flow: fill form, let user review and click submit
      showStatus('addStatus', t('js.infoFound', d.movie.title), 'success');
    }
  } catch(e) {
    showStatus('addStatus', t('js.error', e.message), 'error');
  }
}

async function _fillAddFormFromTmdbId(tmdbId) {
  showStatus('addStatus', t('js.lookingUp'), 'info');
  try {
    const r = await fetch(`${API}/tmdb_movie/${tmdbId}`);
    const d = await r.json();
    if (r.ok && d.movie) {
      _fillAddFields(d.movie);
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
}

async function _lookupBarcodeForAdd(barcode) {
  showStatus('addStatus', t('js.lookingUp'), 'info');
  document.getElementById('addTmdbCandidates').style.display = 'none';
  try {
    const r = await fetch(`${API}/lookup/${barcode}?stream=1`);
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '', finalData = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n'); buf = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const msg = JSON.parse(line);
        if (msg.type === 'step') {
          const icon = msg.status === 'searching' ? '<span class="spinner"></span>' : msg.status === 'hit' ? '✓' : '—';
          showStatus('addStatus', `${icon} ${msg.source}${msg.detail ? ': ' + msg.detail : ''}`, 'info');
        } else if (msg.type === 'done') { finalData = msg; }
      }
    }
    if (!finalData || finalData.error) {
      showStatus('addStatus', finalData?.error || t('js.backendError', ''), 'error'); return;
    }
    if (finalData.status === 'exists') {
      showStatus('addStatus', t('js.alreadyInCollection', finalData.movie.title), 'success'); return;
    }
    if (finalData.status === 'not_found') {
      showStatus('addStatus', t('js.movieNotFound', barcode), 'error'); return;
    }
    const movie = finalData.movie;
    if (finalData.detected_format && !movie.format) movie.format = finalData.detected_format;
    _fillAddFields(movie);
    if (finalData.tmdb_candidates && finalData.tmdb_candidates.length > 1) {
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
  const barcode = document.getElementById('addBarcode').value.trim();
  const title = document.getElementById('addTitle').value.trim();
  if (!barcode && !title) {
    showStatus('addStatus', t('js.barcodeOrTitleRequired'), 'error');
    return;
  }
  // Barcode-only: look up barcode, fill form, then save
  if (barcode && !title) {
    await _lookupBarcodeForAdd(barcode);
    return;
  }
  // Title-only without prior TMDb resolution: show candidates first
  if (title && !barcode && !document.getElementById('addTmdbIdHidden').value.trim()) {
    await autoFillFromTitle();
    return;
  }
  // Both filled, or title + resolved tmdb_id: save
  const effectiveBarcode = barcode ||
    'TITLE-' + title.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 30) + '-' + Date.now().toString().slice(-6);
  await _doSaveManual(effectiveBarcode, title);
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
    steelbook:    'Steel',
    directors_cut: 'DC',
    limited:      'LE',
    theatrical:   'TC',
    '4k_upgrade': '4K+',
    boxset_disc:  'Box',
    dvd:          'DVD',
    bluray:       'BD',
    other:        '…',
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

// Edition group autocomplete in edit modal
let _editionGroupCache = [];

async function searchEditionGroups(query) {
  const dropdown = document.getElementById('editEditionGroupDropdown');
  if (!query || query.length < 1) {
    dropdown.style.display = 'none';
    return;
  }
  if (!_editionGroupCache.length) {
    try {
      const r = await fetch(`${API}/edition-groups`);
      _editionGroupCache = await r.json();
    } catch(e) { _editionGroupCache = []; }
  }
  const q = query.toLowerCase();
  const matches = _editionGroupCache.filter(g => (g.title || '').toLowerCase().includes(q));
  if (!matches.length) {
    dropdown.innerHTML = `<div style="padding:10px 12px; font-size:0.82rem; color:var(--text-muted);">
      <span style="cursor:pointer; color:var(--accent);" onclick="createEditionGroupFromSearch('${query.replace(/'/g,"\\'")}')">+ ${t('edit.editionGroupCreate')} "${query}"</span>
    </div>`;
  } else {
    dropdown.innerHTML = matches.map(g =>
      `<div style="padding:10px 12px; font-size:0.82rem; cursor:pointer; border-bottom:1px solid var(--border);"
            onmousedown="selectEditionGroup(${g.id}, '${(g.title||'').replace(/'/g,"\\'")}')">
        ${g.title} <span style="color:var(--text-muted); font-size:0.76rem;">${g.member_count || 0} ${t('edit.editionGroupMembers')}</span>
       </div>`
    ).join('') + `<div style="padding:8px 12px; font-size:0.78rem; border-top:1px solid var(--border); color:var(--accent); cursor:pointer;"
         onmousedown="createEditionGroupFromSearch('${query.replace(/'/g,"\\'")}')">+ ${t('edit.editionGroupCreate')} "${query}"</div>`;
  }
  dropdown.style.display = 'block';
}

function selectEditionGroup(id, name) {
  document.getElementById('editEditionGroupId').value = id;
  document.getElementById('editEditionGroupSearch').value = '';
  document.getElementById('editEditionGroupDropdown').style.display = 'none';
  const badge = document.getElementById('editEditionGroupBadge');
  document.getElementById('editEditionGroupName').textContent = name;
  badge.style.display = 'flex';
}

function unlinkEditionGroup() {
  document.getElementById('editEditionGroupId').value = '';
  document.getElementById('editEditionGroupSearch').value = '';
  document.getElementById('editEditionGroupBadge').style.display = 'none';
}

async function createEditionGroupFromSearch(title) {
  document.getElementById('editEditionGroupDropdown').style.display = 'none';
  try {
    const r = await fetch(`${API}/edition-groups`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title })
    });
    const g = await r.json();
    _editionGroupCache.push(g);
    selectEditionGroup(g.id, g.title);
  } catch(e) {}
}

async function searchSuperGroup(query) {
  const dropdown = document.getElementById('editSuperGroupDropdown');
  if (!query || query.length < 1) { dropdown.style.display = 'none'; return; }
  try {
    const r = await fetch(`${API}/edition-groups?q=${encodeURIComponent(query)}`);
    const groups = await r.json();
    const items = groups.slice(0, 8).map(g =>
      `<div style="padding:10px 12px; font-size:0.82rem; cursor:pointer; border-bottom:1px solid var(--border);"
            onmousedown="selectSuperGroup(${g.id}, '${(g.title||'').replace(/'/g,"\\'")}')">
        ${g.title} <span style="color:var(--text-muted); font-size:0.76rem;">${g.member_count || 0} edities</span>
       </div>`
    );
    items.push(`<div style="padding:8px 12px; font-size:0.78rem; border-top:1px solid var(--border); color:var(--accent2); cursor:pointer;"
         onmousedown="createAndSelectSuperGroup('${query.replace(/'/g,"\\'")}')">+ Maak "${query}" aan als nieuwe groep</div>`);
    dropdown.innerHTML = items.join('');
    dropdown.style.display = 'block';
  } catch(e) {}
}

async function createAndSelectSuperGroup(title) {
  document.getElementById('editSuperGroupDropdown').style.display = 'none';
  try {
    const r = await fetch(`${API}/edition-groups`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ title })
    });
    const g = await r.json();
    selectSuperGroup(g.id, g.title);
  } catch(e) {}
}

function selectSuperGroup(id, name) {
  document.getElementById('editSuperGroupId').value = id;
  document.getElementById('editSuperGroupSearch').value = '';
  document.getElementById('editSuperGroupDropdown').style.display = 'none';
  document.getElementById('editSuperGroupName').textContent = name;
  document.getElementById('editSuperGroupBadge').style.display = 'flex';
}

function unlinkSuperGroup() {
  document.getElementById('editSuperGroupId').value = '';
  document.getElementById('editSuperGroupSearch').value = '';
  document.getElementById('editSuperGroupBadge').style.display = 'none';
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
      const sources = (entry.digital_matches || []).map(x =>
        `<span style="font-size:0.72rem; background:rgba(${x.source_type==='plex'?'232,197,71':'124,106,247'},.15); color:var(--${x.source_type==='plex'?'accent':'accent2'}); border:1px solid rgba(${x.source_type==='plex'?'232,197,71':'124,106,247'},.3); border-radius:4px; padding:1px 6px;">${x.source_name}</span>`
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
      items.map(item =>
        `<div style="display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface2); border:1px solid var(--border); border-radius:8px;">
          <div style="flex:1; min-width:0;">
            <div style="font-weight:500; font-size:0.9rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${item.title}</div>
            <div style="font-size:0.78rem; color:var(--text-muted);">${item.year||'—'}</div>
          </div>
          <span style="font-size:0.72rem; background:rgba(${item.source_type==='plex'?'232,197,71':'124,106,247'},.15); color:var(--${item.source_type==='plex'?'accent':'accent2'}); border:1px solid rgba(${item.source_type==='plex'?'232,197,71':'124,106,247'},.3); border-radius:4px; padding:2px 8px; flex-shrink:0;">${item.source_name}</span>
        </div>`
      ).join('') + `</div>`;
  }
}
