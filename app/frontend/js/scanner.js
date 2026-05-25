// ── Barcode Scanner ───────────────────────────────────────────────────────────
let _usingNative = false;
let _nativeStream = null;
let _nativeTimer = null;
let _detectedFormat = '';   // format detected from UPC/EAN title, persists across candidate selection
let currentBoxSetProposal = null;
let _lookupInFlightBarcode = '';

function _supportsNativeDetector() {
  return typeof BarcodeDetector !== 'undefined';
}

// Validate EAN-13 and UPC-A checksums — the only formats used on disc media
function _validateBarcode(code) {
  if (/^\d{13}$/.test(code)) {
    // EAN-13 (worldwide standard for DVD/Blu-ray/4K UHD)
    let s = 0;
    for (let i = 0; i < 12; i++) s += parseInt(code[i]) * (i % 2 === 0 ? 1 : 3);
    return (10 - (s % 10)) % 10 === parseInt(code[12]);
  }
  if (/^\d{12}$/.test(code)) {
    // UPC-A (North American releases; effectively EAN-13 with leading zero omitted)
    let s = 0;
    for (let i = 0; i < 11; i++) s += parseInt(code[i]) * (i % 2 === 0 ? 3 : 1);
    return (10 - (s % 10)) % 10 === parseInt(code[11]);
  }
  return false; // EAN-8, UPC-E etc. are never used on disc media — reject
}

// Try to apply continuous autofocus to a video track (best-effort)
async function _applyFocusConstraints(stream) {
  try {
    const track = stream.getVideoTracks()[0];
    if (!track || !track.getCapabilities) return;
    const caps = track.getCapabilities();
    const adv = {};
    if (caps.focusMode && caps.focusMode.includes('continuous')) adv.focusMode = 'continuous';
    if (caps.exposureMode && caps.exposureMode.includes('continuous')) adv.exposureMode = 'continuous';
    if (caps.whiteBalanceMode && caps.whiteBalanceMode.includes('continuous')) adv.whiteBalanceMode = 'continuous';
    if (Object.keys(adv).length > 0) await track.applyConstraints({ advanced: [adv] });
  } catch(e) { /* not supported on this device */ }
}

function _loadQuagga2() {
  return new Promise((resolve, reject) => {
    if (window.Quagga) { resolve(); return; }
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/@ericblade/quagga2@1.8.4/dist/quagga.min.js';
    s.onload = resolve;
    s.onerror = () => reject(new Error('Quagga2 load failed'));
    document.head.appendChild(s);
  });
}

function _resetScannerUI() {
  document.getElementById('btnStartScan').style.display = 'inline-flex';
  document.getElementById('btnStopScan').style.display = 'none';
  const container = document.getElementById('scanner-container');
  const video = container.querySelector('video');
  if (video) video.remove();
  const overlay = container.querySelector('.scanner-overlay');
  if (overlay) overlay.remove();
  const ph = document.getElementById('scannerPlaceholder');
  ph.style.display = 'flex';
  ph.style.flexDirection = 'column';
  ph.style.alignItems = 'center';
}

async function _tryNativeScanner(container) {
  let detector;
  try {
    detector = new BarcodeDetector({ formats: ['ean_13', 'upc_a'] });
  } catch(e) {
    return 'fallback';
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: 'environment',
        width:  { ideal: 1920 },
        height: { ideal: 1080 }
      }
    });
  } catch(e) {
    showStatus('scanStatus', t('js.cameraError', e.message), 'error');
    _resetScannerUI();
    return 'error';
  }

  _usingNative = true;
  _nativeStream = stream;
  await _applyFocusConstraints(stream);

  const video = document.createElement('video');
  video.setAttribute('playsinline', ''); // required for iOS
  video.setAttribute('autoplay', '');    // required for iOS PWA autoplay
  video.setAttribute('muted', '');       // attribute form required for iOS autoplay policy
  video.muted = true;
  video.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:8px;';
  // iOS requires the element to be in the DOM *before* srcObject is set
  container.appendChild(video);
  video.srcObject = stream;

  try {
    await video.play();
  } catch(playErr) {
    // play() failed (e.g. iOS autoplay restriction) — fall back to Quagga2
    _stopNativeScanner();
    return 'fallback';
  }

  const overlay = document.createElement('div');
  overlay.className = 'scanner-overlay';
  overlay.innerHTML = '<div class="scanner-frame"><div class="scan-line"></div></div>';
  container.appendChild(overlay);

  scannerRunning = true;

  let lastCode = '';
  let lastTime = 0;
  let _confirmCode = '';
  let _confirmCount = 0;

  async function runDetect() {
    if (!scannerRunning || !_usingNative) return;
    try {
      const results = await detector.detect(video);
      if (results.length === 0) { _confirmCode = ''; _confirmCount = 0; }
      for (const barcode of results) {
        const code = barcode.rawValue;
        if (!_validateBarcode(code)) continue; // reject checksums that don't add up
        const now = Date.now();
        if (code === lastCode && now - lastTime < 3000) continue;
        // Require 3 consecutive detections of the same code to filter false positives
        if (code === _confirmCode) {
          _confirmCount++;
        } else {
          _confirmCode = code;
          _confirmCount = 1;
        }
        if (_confirmCount < 3) break;
        lastCode = code; lastTime = now;
        _confirmCode = ''; _confirmCount = 0;
        stopScanner();
        document.getElementById('manualBarcode').value = code;
        doLookup(code);
        return;
      }
    } catch(e) { /* video not ready yet */ }
    if (scannerRunning && _usingNative) _nativeTimer = setTimeout(runDetect, 150);
  }
  _nativeTimer = setTimeout(runDetect, 1000); // 1s initial delay: time for camera autofocus
  return 'success';
}

async function startScanner() {
  const container = document.getElementById('scanner-container');
  document.getElementById('scannerPlaceholder').style.display = 'none';
  document.getElementById('btnStartScan').style.display = 'none';
  document.getElementById('btnStopScan').style.display = 'inline-flex';

  if (_supportsNativeDetector()) {
    const result = await _tryNativeScanner(container);
    if (result !== 'fallback') return;
  }

  // Fallback: Quagga2 (lazy-loaded)
  _usingNative = false;
  try {
    await _loadQuagga2();
  } catch(e) {
    showStatus('scanStatus', t('js.cameraError', e.message), 'error');
    _resetScannerUI();
    return;
  }

  Quagga.init({
    inputStream: {
      name: 'Live',
      type: 'LiveStream',
      target: container,
      constraints: {
        facingMode: 'environment',
        width:  { ideal: 1920 },
        height: { ideal: 1080 }
      }
    },
    decoder: {
      readers: ['ean_reader', 'upc_reader'],
      multiple: false
    },
    locate: true,
    frequency: 10
  }, function(err) {
    if (err) {
      showStatus('scanStatus', t('js.cameraError', err.message), 'error');
      stopScanner();
      return;
    }
    // iOS fix: Quagga2 doesn't add playsinline/muted to its internal <video>,
    // which causes a black screen on iOS Safari. Inject the attributes before start.
    const quaggaVideo = container.querySelector('video');
    if (quaggaVideo) {
      quaggaVideo.setAttribute('playsinline', '');
      quaggaVideo.setAttribute('muted', '');
      quaggaVideo.muted = true;
    }
    Quagga.start();
    scannerRunning = true;

    const overlay = document.createElement('div');
    overlay.className = 'scanner-overlay';
    overlay.innerHTML = '<div class="scanner-frame"><div class="scan-line"></div></div>';
    container.appendChild(overlay);

    // Apply continuous autofocus after Quagga has acquired the stream
    const quaggaStream = quaggaVideo && quaggaVideo.srcObject;
    if (quaggaStream) _applyFocusConstraints(quaggaStream);
  });

  let lastCode = '';
  let lastTime = 0;
  let _quaggaConfirmCode = '';
  let _quaggaConfirmCount = 0;
  let _quaggaResetTimer = null;
  Quagga.onDetected(result => {
    const code = result.codeResult.code;
    if (!_validateBarcode(code)) return; // reject invalid checksums

    // Confidence filter: average bar-decode error must be below threshold
    const bars = (result.codeResult.decodedCodes || []).filter(b => b.error !== undefined);
    if (bars.length > 0) {
      const avgErr = bars.reduce((s, b) => s + b.error, 0) / bars.length;
      if (avgErr > 0.08) return; // too uncertain — skip this frame
    }

    const now = Date.now();
    if (code === lastCode && now - lastTime < 3000) return;

    // Require 4 consecutive consistent reads; reset counter if no match within 600ms
    if (_quaggaResetTimer) clearTimeout(_quaggaResetTimer);
    _quaggaResetTimer = setTimeout(() => { _quaggaConfirmCode = ''; _quaggaConfirmCount = 0; }, 600);

    if (code === _quaggaConfirmCode) {
      _quaggaConfirmCount++;
    } else {
      _quaggaConfirmCode = code;
      _quaggaConfirmCount = 1;
    }
    if (_quaggaConfirmCount < 4) return;
    clearTimeout(_quaggaResetTimer);
    _quaggaConfirmCode = '';
    _quaggaConfirmCount = 0;
    lastCode = code;
    lastTime = now;
    stopScanner();
    document.getElementById('manualBarcode').value = code;
    doLookup(code);
  });
}

function stopScanner() {
  if (_usingNative) {
    if (_nativeTimer) { clearTimeout(_nativeTimer); _nativeTimer = null; }
    if (_nativeStream) { _nativeStream.getTracks().forEach(t => t.stop()); _nativeStream = null; }
    _usingNative = false;
  } else if (scannerRunning) {
    try { Quagga.stop(); } catch(e) {}
  }
  scannerRunning = false;
  _resetScannerUI();
}

function lookupBarcode() {
  const val = document.getElementById('manualBarcode').value.trim();
  if (!val) return;
  doLookup(val);
}

// Detect disc format from a title string (mirrors backend _detect_format_from_upc logic)
function _detectFormatFromTitle(title) {
  const t = (title || '').toLowerCase();
  if (/4k[\s-]?uhd|ultra[\s-]*hd|\buhd\b/.test(t)) return '4K UHD';
  if (/\b4k\b/.test(t) && /blu[- ]?ray/.test(t)) return '4K UHD';
  if (/\b4k\b/.test(t)) return '4K UHD';
  if (/blu[- ]?ray/.test(t)) return 'Blu-ray';
  if (/\bdvd\b/.test(t)) return 'DVD';
  return '';
}

function _lookupStreamErrorText(text, status) {
  const plain = String(text || '')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return plain ? `HTTP ${status}: ${plain.slice(0, 180)}` : `HTTP ${status}`;
}

async function readLookupNdjson(response, onMessage) {
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(_lookupStreamErrorText(text, response.status));
  }
  if (!response.body || !response.body.getReader) {
    throw new Error('Lookup stream unavailable');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  const parseLine = (line) => {
    try {
      return JSON.parse(line);
    } catch (e) {
      if (line.trim().startsWith('<')) {
        throw new Error('Backend returned HTML instead of lookup data');
      }
      throw new Error(`Invalid lookup stream response: ${e.message}`);
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      onMessage(parseLine(line));
    }
  }

  buf += decoder.decode();
  if (buf.trim()) {
    onMessage(parseLine(buf));
  }
}

async function doLookup(barcode) {
  barcode = String(barcode || '').trim();
  if (!barcode) return;
  if (_lookupInFlightBarcode) {
    if (_lookupInFlightBarcode === barcode) {
      showStatus('scanStatus', t('js.lookingUp'), 'info');
      return;
    }
    showStatus('scanStatus', t('js.lookingUp'), 'info');
    return;
  }
  _lookupInFlightBarcode = barcode;
  showStatus('scanStatus', t('js.lookingUp'), 'info');
  document.getElementById('movieResult').style.display = 'none';
  document.getElementById('noResult').style.display = 'none';
  document.getElementById('tmdbCandidates').style.display = 'none';
  hideBoxSetProposal();

  try {
    const r = await fetch(`${API}/lookup/${barcode}?stream=1`);
    let finalData = null, stepRawTitle = '';

    await readLookupNdjson(r, (msg) => {
        if (msg.type === 'step') {
          if (msg.raw_title) stepRawTitle = msg.raw_title;
          const icon = msg.status === 'searching' ? '<span class="spinner"></span>'
            : msg.status === 'hit' ? '✓' : msg.status === 'miss' ? '—' : '✕';
          showStatus('scanStatus', `${icon} ${msg.source}${msg.detail ? ': ' + msg.detail : ''}`, msg.status === 'hit' ? 'success' : 'info');
        } else if (msg.type === 'done') {
          finalData = msg;
        }
    });

    if (!finalData) {
      showStatus('scanStatus', t('js.backendError', r.status), 'error');
      document.getElementById('noResult').style.display = 'block';
      return;
    }
    if (finalData.error) {
      showStatus('scanStatus', t('js.error', finalData.error), 'error');
      document.getElementById('noResult').style.display = 'block';
      return;
    }

    if (finalData.status === 'movie_exists' || finalData.status === 'exists') {
      showStatus('scanStatus', t('js.alreadyInCollection', finalData.movie.title), 'success');
      _detectedFormat = finalData.detected_format || '';
      displayMovieResult(finalData.movie, barcode, true, _detectedFormat);
    } else if (finalData.status === 'box_set_exists' || finalData.status === 'vault_exists') {
      const isVault = finalData.status === 'vault_exists';
      const container = isVault ? finalData.vault : finalData.box_set;
      const title = container?.title || barcode;
      const label = isVault ? 'Vault' : 'Box-set';
      showStatus('scanStatus', t('js.alreadyInCollection', title), 'success');
      currentBarcode = barcode;
      currentMovieData = { title, barcode, format: label };
      document.getElementById('resultTitle').textContent = title;
      const tags = document.getElementById('resultTags');
      tags.innerHTML = `<span class="tag format">${label}</span>`;
      const poster = document.getElementById('resultPoster');
      poster.classList.remove('is-clickable');
      poster.onclick = null;
      poster.title = '';
      poster.innerHTML = '<div class="no-poster">📦</div>';
      document.getElementById('movieResult').style.display = 'flex';
      document.getElementById('noResult').style.display = 'none';
      document.getElementById('btnSave').style.display = 'none';
      resetScanSupplementButton();
    } else if (finalData.status === 'found') {
      _detectedFormat = finalData.detected_format
        || _detectFormatFromTitle(finalData.raw_title || stepRawTitle)
        || '';
      const fmtLabel = _detectedFormat ? ` · ${_detectedFormat}` : '';
      showStatus('scanStatus', t('js.movieFound') + fmtLabel, 'success');
      displayMovieResult(finalData.movie, barcode, false, _detectedFormat);
      if (finalData.box_set_proposal && finalData.box_set_proposal.movies && finalData.box_set_proposal.movies.length) {
        displayBoxSetProposal(finalData.box_set_proposal, barcode, _detectedFormat);
      }
      if (finalData.tmdb_candidates && finalData.tmdb_candidates.length > 1) {
        displayTmdbCandidates(finalData.tmdb_candidates, barcode);
      }
    } else {
      // Barcode not found: show empty placeholder so user can still supplement
      _detectedFormat = finalData.detected_format
        || _detectFormatFromTitle(finalData.raw_title || stepRawTitle)
        || '';
      const fmtLabel = _detectedFormat ? ` · ${_detectedFormat}` : '';
      showStatus('scanStatus', t('js.movieNotFound', barcode) + fmtLabel, 'error');
      currentBarcode = barcode;
      currentMovieData = { title: finalData.raw_title || '', barcode };
      if (_detectedFormat) currentMovieData.format = _detectedFormat;
      document.getElementById('resultTitle').textContent = finalData.raw_title || barcode;
      const tags = document.getElementById('resultTags');
      tags.innerHTML = '';
      if (_detectedFormat) tags.innerHTML += `<span class="tag format">${_detectedFormat}</span>`;
      const poster = document.getElementById('resultPoster');
      poster.classList.remove('is-clickable');
      poster.onclick = null;
      poster.title = '';
      poster.innerHTML = '<div class="no-poster">🎬</div>';
      document.getElementById('movieResult').style.display = 'flex';
      document.getElementById('noResult').style.display = 'none';
      document.getElementById('btnSave').style.display = 'none';
      resetScanSupplementButton();
    }
  } catch(e) {
    showStatus('scanStatus', t('js.connectionError', e.message), 'error');
    document.getElementById('noResult').style.display = 'block';
  } finally {
    if (_lookupInFlightBarcode === barcode) _lookupInFlightBarcode = '';
  }
}

function _escapeHtml(value) {
  return String(value || '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[ch]));
}

function hideBoxSetProposal() {
  currentBoxSetProposal = null;
  const panel = document.getElementById('boxSetProposal');
  if (panel) panel.style.display = 'none';
}

function apiImageUrl(value, kind = 'image') {
  const raw = String(value || '').trim();
  if (!raw || raw === 'N/A') return '';
  if (/^https?:\/\//i.test(raw) || /^data:/i.test(raw)) return raw;
  if (raw.startsWith('/api/images/')) return raw;
  if (raw.startsWith('/api/posters/offline/')) {
    const fileName = raw.split('/').pop();
    return fileName ? `${API}/images/offline/poster/${encodeURIComponent(fileName)}` : raw;
  }
  if (raw.startsWith('/api/posters/')) {
    const fileName = raw.split('/').pop();
    return fileName ? `${API}/images/${encodeURIComponent(fileName)}` : raw;
  }
  if (raw.startsWith('/api/profiles/offline/')) {
    const fileName = raw.split('/').pop();
    return fileName ? `${API}/images/profiles/offline/profile/${encodeURIComponent(fileName)}` : raw;
  }
  if (raw.startsWith('/api/profiles/')) {
    const fileName = raw.split('/').pop();
    return fileName ? `${API}/images/profiles/${encodeURIComponent(fileName)}` : raw;
  }
  if (raw.startsWith('/api/')) return raw;
  if (raw.startsWith('/') && kind === 'tmdbPoster') {
    return `https://image.tmdb.org/t/p/w342${raw}`;
  }
  const fileName = raw.split(/[/\\]/).pop();
  if (!fileName) return raw;
  if (kind === 'profile') return `${API}/images/profiles/${encodeURIComponent(fileName)}`;
  if (kind === 'posterOffline') return `${API}/images/offline/poster/${encodeURIComponent(fileName)}`;
  if (kind === 'backdropOffline') return `${API}/images/offline/backdrop/${encodeURIComponent(fileName)}`;
  return `${API}/images/${encodeURIComponent(fileName)}`;
}

function firstImageUrl(values, kind = 'image') {
  for (const value of values) {
    const url = apiImageUrl(value, kind);
    if (url) return url;
  }
  return '';
}

function profileSrc(person) {
  if (!person) return '';
  return firstImageUrl([
    person.photoUrl,
    person.photo_url,
    person.profileUrl,
    person.profile_url,
    person.photo,
  ], 'profile') || apiImageUrl(person.photoFile || person.photo_file, 'profile');
}

function _boxSetMemberPosterSrc(movie) {
  return posterSrc(movie) || '';
}

function displayBoxSetProposal(proposal, barcode, detectedFormat) {
  const movies = (proposal.movies || []).filter(m => m && m.title).slice(0, 50);
  if (!movies.length) {
    hideBoxSetProposal();
    return;
  }
  currentBoxSetProposal = {
    box_set_title: proposal.title || proposal.name || currentMovieData.title || `Box Set ${barcode}`,
    barcode,
    format: detectedFormat || currentMovieData.format || '4K UHD',
    source: proposal.source || 'Blu-ray.com',
    detail_url: proposal.detail_url || '',
    movies,
    selected_indices: movies.map((_, idx) => idx),
    saved_indices: []
  };

  const nameEl = document.getElementById('boxSetProposalName');
  const listEl = document.getElementById('boxSetProposalList');
  const panel = document.getElementById('boxSetProposal');
  const btn = document.getElementById('btnSaveBoxSetProposal');
  if (!nameEl || !listEl || !panel || !btn) return;

  nameEl.textContent = '';
  listEl.innerHTML = movies.map((m, idx) => `
    <div class="boxset-member-row">
      <input class="boxset-member-check" type="checkbox" id="scanBoxSetMovieSelect${idx}" checked onchange="toggleBoxSetProposalMovie(${idx}, this.checked)" aria-label="${_escapeHtml(m.title)}">
      <span class="boxset-member-index">${idx + 1}</span>
      <span class="boxset-member-cover">${_boxSetMemberPosterSrc(m) ? `<img src="${_escapeHtml(_boxSetMemberPosterSrc(m))}" alt="">` : '🎬'}</span>
      <strong class="boxset-member-title">${_escapeHtml(m.title)}</strong>
      ${m.year ? `<span class="tag">${_escapeHtml(m.year)}</span>` : ''}
      <button class="btn btn-secondary boxset-member-save" id="scanBoxSetMovieBtn${idx}" onclick="saveBoxSetProposalMovie(${idx})">Opslaan</button>
    </div>
  `).join('');
  btn.disabled = false;
  btn.textContent = t('scan.boxSetProposalSave');
  panel.style.display = 'block';
  const btnSave = document.getElementById('btnSave');
  if (btnSave) btnSave.style.display = 'none';
  const btnSupplement = document.getElementById('btnSupplement');
  if (btnSupplement) btnSupplement.style.display = 'none';
  updateBoxSetProposalSelection();
}

function updateBoxSetProposalSelection() {
  if (!currentBoxSetProposal) return;
  const selectedCount = currentBoxSetProposal.selected_indices
    .filter(idx => !currentBoxSetProposal.saved_indices.includes(idx)).length;
  const nameEl = document.getElementById('boxSetProposalName');
  if (nameEl) {
    nameEl.textContent = `${currentBoxSetProposal.box_set_title} · ${selectedCount} films gevonden`;
  }
  const saveAllBtn = document.getElementById('btnSaveBoxSetProposal');
  if (saveAllBtn) {
    saveAllBtn.disabled = selectedCount === 0;
  }
  currentBoxSetProposal.movies.forEach((_, idx) => {
    const rowBtn = document.getElementById(`scanBoxSetMovieBtn${idx}`);
    if (rowBtn && !currentBoxSetProposal.saved_indices.includes(idx)) {
      rowBtn.disabled = !currentBoxSetProposal.selected_indices.includes(idx);
    }
  });
}

function toggleBoxSetProposalMovie(index, checked) {
  if (!currentBoxSetProposal) return;
  if (checked) {
    if (!currentBoxSetProposal.selected_indices.includes(index)) {
      currentBoxSetProposal.selected_indices.push(index);
    }
  } else {
    currentBoxSetProposal.selected_indices = currentBoxSetProposal.selected_indices.filter(idx => idx !== index);
  }
  updateBoxSetProposalSelection();
}

async function _saveBoxSetProposalMovies(indices) {
  if (!currentBoxSetProposal) return;
  const movies = indices
    .filter(idx => !currentBoxSetProposal.saved_indices.includes(idx))
    .map(idx => currentBoxSetProposal.movies[idx])
    .filter(Boolean);
  if (!movies.length) return;
  const payload = {
    ...currentBoxSetProposal,
    movies,
    box_set_id: currentBoxSetProposal.box_set_id || null
  };
  const r = await fetch(`${API}/box-set-proposals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || t('js.saveFailed'));
  currentBoxSetProposal.box_set_id = d.box_set.id;
  indices.forEach(idx => {
    if (!currentBoxSetProposal.saved_indices.includes(idx)) {
      currentBoxSetProposal.saved_indices.push(idx);
    }
    const rowBtn = document.getElementById(`scanBoxSetMovieBtn${idx}`);
    if (rowBtn) {
      rowBtn.disabled = true;
      rowBtn.textContent = 'Opgeslagen';
      rowBtn.classList.add('is-saved');
    }
    const rowCheck = document.getElementById(`scanBoxSetMovieSelect${idx}`);
    if (rowCheck) {
      rowCheck.checked = true;
      rowCheck.disabled = true;
    }
  });
  updateBoxSetProposalSelection();
  if (typeof loadStats === 'function') loadStats();
  if (typeof loadCollection === 'function') loadCollection();
  return d;
}

async function saveBoxSetProposalMovie(index) {
  const btn = document.getElementById(`scanBoxSetMovieBtn${index}`);
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
  }
  try {
    const d = await _saveBoxSetProposalMovies([index]);
    showStatus('scanStatus', t('scan.boxSetProposalSaved', d.box_set.title, d.movies.length), 'success');
  } catch(e) {
    showStatus('scanStatus', t('js.error', e.message), 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Opslaan';
    }
  }
}

async function saveBoxSetProposal() {
  if (!currentBoxSetProposal) return;
  const btn = document.getElementById('btnSaveBoxSetProposal');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> ' + t('scan.boxSetProposalSaving');
  }
  try {
    const indices = currentBoxSetProposal.selected_indices.slice();
    const d = await _saveBoxSetProposalMovies(indices);
    showStatus('scanStatus', t('scan.boxSetProposalSaved', d.box_set.title, d.movies.length), 'success');
    document.getElementById('movieResult').style.display = 'none';
    document.getElementById('noResult').style.display = 'block';
    hideBoxSetProposal();
  } catch(e) {
    showStatus('scanStatus', t('js.error', e.message), 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('scan.boxSetProposalSave');
    }
  }
}

function displayTmdbCandidates(candidates, barcode) {
  const esc = _escapeHtml;
  const safeBarcode = String(barcode).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  document.getElementById('tmdbCandidateList').innerHTML = candidates.map(c => `
    <div class="tmdb-candidate-card" onclick="selectTmdbCandidate('${c.tmdb_id}','${safeBarcode}')">
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
  document.getElementById('tmdbCandidates').style.display = 'block';
}

async function selectTmdbCandidate(tmdbId, barcode) {
  document.getElementById('tmdbCandidates').style.display = 'none';
  showStatus('scanStatus', t('js.lookingUp'), 'info');
  try {
    const r = await fetch(`${API}/tmdb_movie/${tmdbId}`);
    const d = await r.json();
    if (r.ok && d.movie) {
      displayMovieResult(d.movie, barcode, false, _detectedFormat);
      const fmtLabel = _detectedFormat ? ` · ${_detectedFormat}` : '';
      showStatus('scanStatus', t('js.movieFound') + fmtLabel, 'success');
    } else {
      showStatus('scanStatus', d.error || t('js.backendError', r.status), 'error');
    }
  } catch(e) {
    showStatus('scanStatus', t('js.connectionError', e.message), 'error');
  }
}

// Returns the best available poster URL for a movie object
function posterSrc(m) {
  if (!m) return null;
  // Container cards (vault / box-set / collection) store their own uploaded
  // poster in _container_poster_file so the primary film's poster_file is
  // never overwritten.  Use _container_poster_file first for those cards.
  const containerPoster = apiImageUrl(m._container_poster_file, 'poster');
  if (containerPoster) return containerPoster;
  const filePoster = apiImageUrl(m.posterFile || m.poster_file, 'poster');
  if (filePoster) return filePoster;
  const responsePoster = firstImageUrl([
    m.posterUrl,
    m.poster_url,
    m.coverUrl,
    m.cover_url,
    m.poster,
  ], 'poster');
  if (responsePoster) return responsePoster;
  const tmdbPoster = apiImageUrl(m.posterPath || m.poster_path, 'tmdbPoster');
  if (tmdbPoster) return tmdbPoster;
  return null;
}

function backdropSrc(valueOrMovie) {
  if (!valueOrMovie) return '';
  if (typeof valueOrMovie === 'string') return apiImageUrl(valueOrMovie, 'backdrop');
  return firstImageUrl([
    valueOrMovie.backdropUrl,
    valueOrMovie.backdrop_url,
    valueOrMovie.backdrop,
  ], 'backdrop');
}

function displayMovieResult(movie, barcode, alreadyInCollection, detectedFormat) {
  currentBarcode = barcode;
  currentMovieData = { ...movie, barcode };
  if (detectedFormat && !currentMovieData.format) currentMovieData.format = detectedFormat;

  document.getElementById('resultTitle').textContent = movie.title || '—';

  const tags = document.getElementById('resultTags');
  tags.innerHTML = '';
  const fmt = currentMovieData.format || detectedFormat;
  if (fmt)        tags.innerHTML += `<span class="tag format">${fmt}</span>`;
  if (movie.year)     tags.innerHTML += `<span class="tag">${movie.year}</span>`;
  if (movie.director) tags.innerHTML += `<span class="tag">${movie.director}</span>`;
  if (movie.genre)    movie.genre.split(',').slice(0, 2).forEach(g => { tags.innerHTML += `<span class="tag">${g.trim()}</span>`; });
  if (movie.rating)   tags.innerHTML += `<span class="tag">⭐ ${movie.rating}</span>`;
  if (movie.runtime)  tags.innerHTML += `<span class="tag">${movie.runtime} min</span>`;

  const src = posterSrc(movie);
  const poster = document.getElementById('resultPoster');
  poster.classList.toggle('is-clickable', !!(alreadyInCollection && movie.id));
  poster.onclick = alreadyInCollection && movie.id ? () => openScannedMovieDetail(movie.id) : null;
  poster.title = alreadyInCollection && movie.id ? t('scan.view') : '';
  if (src) {
    poster.innerHTML = `<img src="${src}" onerror="this.parentElement.innerHTML='<div class=\\'no-poster\\'>🎬</div>'">`;
  } else {
    poster.innerHTML = '<div class="no-poster">🎬</div>';
  }

  document.getElementById('movieResult').style.display = 'flex';
  document.getElementById('noResult').style.display = 'none';

  const btnSave = document.getElementById('btnSave');
  btnSave.style.display = alreadyInCollection ? 'none' : '';
  btnSave.disabled = false;
  if (!alreadyInCollection) btnSave.innerHTML = t('scan.save');

  const btnSupplement = document.getElementById('btnSupplement');
  if (btnSupplement) {
    btnSupplement.style.display = '';
    btnSupplement.disabled = false;
    if (alreadyInCollection && movie.id) {
      btnSupplement.innerHTML = t('scan.view');
      btnSupplement.onclick = () => openScannedMovieDetail(movie.id);
    } else {
      btnSupplement.innerHTML = t('scan.supplement');
      btnSupplement.onclick = supplementMovie;
    }
  }
}

function openScannedMovieDetail(movieId) {
  if (!movieId) return;
  if (typeof openMovieDetail === 'function') {
    openMovieDetail(movieId);
  }
}

function resetScanSupplementButton() {
  const btnSupplement = document.getElementById('btnSupplement');
  if (!btnSupplement) return;
  btnSupplement.style.display = '';
  btnSupplement.disabled = false;
  btnSupplement.innerHTML = t('scan.supplement');
  btnSupplement.onclick = supplementMovie;
}

async function saveMovie() {
  const btn = document.getElementById('btnSave');
  btn.innerHTML = '<span class="spinner"></span>';
  btn.disabled = true;

  const payload = { ...currentMovieData, barcode: currentBarcode };

  try {
    const r = await fetch(`${API}/movies`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (r.ok) {
      if (d.queued) {
        showStatus('scanStatus', t('js.queuedSave', payload.title || t('js.unknownMovie')), 'info');
        btn.innerHTML = t('js.inQueue');
        btn.disabled = true;
      } else {
        showStatus('scanStatus', t('js.savedToCollection', d.movie.title), 'success');
        document.getElementById('movieResult').style.display = 'none';
        document.getElementById('noResult').style.display = 'block';
        loadStats();
        // Show edition link prompt if duplicate TMDb ID detected
        if (d.duplicate_tmdb_hint && d.duplicate_tmdb_hint.existing_movies && d.duplicate_tmdb_hint.existing_movies.length) {
          _showEditionLinkPrompt(d.movie, d.duplicate_tmdb_hint);
        }
      }
    } else {
      showStatus('scanStatus', d.error || t('js.saveFailed'), 'error');
      btn.innerHTML = t('js.saveToCollectionBtn');
      btn.disabled = false;
    }
  } catch(e) {
    showStatus('scanStatus', t('js.error', e.message), 'error');
    btn.innerHTML = t('js.saveToCollectionBtn');
    btn.disabled = false;
  }
}

let _editionLinkNewMovie  = null;
let _editionLinkHint      = null;

function _editionPromptLabel(movie) {
  const type = movie?.edition_type || 'standard';
  if (type === 'standard') return '';
  if (type === 'custom') return movie?.custom_edition_label || t('edition.custom');
  const labels = {
    steelbook: t('edition.steelbook'),
    directors_cut: t('edition.directorsCut'),
    limited: t('edition.limited'),
    theatrical: t('edition.theatrical'),
    '4k_upgrade': t('edition.4kUpgrade'),
    '4k_combo': t('edition.4kCombo'),
    boxset_disc: t('edition.boxsetDisc'),
    dvd: 'DVD',
    bluray: 'Blu-ray',
  };
  return labels[type] || String(type).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function _showEditionLinkPrompt(newMovie, hint) {
  _editionLinkNewMovie = newMovie;
  _editionLinkHint     = hint;
  const modal = document.getElementById('editionLinkModal');
  const desc  = document.getElementById('editionLinkDesc');
  const existing = document.getElementById('editionLinkExisting');
  if (!modal) return;
  desc.textContent = t('edition.linkDesc', newMovie.title);
  existing.innerHTML = hint.existing_movies.map(m => {
    const editionLabel = _editionPromptLabel(m);
    return `<div style="padding:8px 12px; background:var(--surface2); border:1px solid var(--border); border-radius:6px; margin-bottom:6px; font-size:0.84rem;">
      <strong>${m.title}</strong> · ${m.format || ''}
      ${editionLabel ? `<span style="color:var(--accent); font-size:0.76rem;"> · ${editionLabel}</span>` : ''}
    </div>`;
  }).join('');
  modal.style.display = 'flex';
}

function closeEditionLinkModal() {
  const modal = document.getElementById('editionLinkModal');
  if (modal) modal.style.display = 'none';
  _editionLinkNewMovie = null;
  _editionLinkHint     = null;
}

async function confirmEditionLink() {
  if (!_editionLinkNewMovie || !_editionLinkHint) return;
  const btn = document.getElementById('editionLinkBtn');
  btn.disabled = true;
  try {
    let groupId = _editionLinkHint.suggested_group_id;
    if (!groupId) {
      // Create a new edition group for this film
      const r = await fetch(`${API}/edition-groups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: _editionLinkNewMovie.title,
          tmdb_id: _editionLinkNewMovie.tmdb_id,
          imdb_id: _editionLinkNewMovie.imdb_id,
          year: _editionLinkNewMovie.year,
        })
      });
      const g = await r.json();
      groupId = g.id;
      // Add all existing movies with same tmdb_id to the group
      const existingIds = _editionLinkHint.existing_movies.map(m => m.id);
      await fetch(`${API}/edition-groups/${groupId}/members`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ movie_ids: existingIds })
      });
    }
    // Add the newly scanned movie to the group
    await fetch(`${API}/edition-groups/${groupId}/members`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ movie_ids: [_editionLinkNewMovie.id] })
    });
    closeEditionLinkModal();
    if (typeof loadCollection === 'function') loadCollection();
  } catch(e) {
    btn.disabled = false;
  }
}

function supplementMovie() {
  // Switch to manual add tab and pre-fill all available data from the scan result
  if (typeof switchToevoegen === 'function') switchToevoegen('manual');
  const addBarcodeEl = document.getElementById('addBarcode');
  if (addBarcodeEl) addBarcodeEl.value = currentBarcode || '';
  if (currentMovieData && typeof _fillAddFields === 'function') {
    _fillAddFields(currentMovieData);
    if (typeof showStatus === 'function' && currentMovieData.title) {
      showStatus('addStatus', t('js.infoFound', currentMovieData.title), 'success');
    }
  }
}
