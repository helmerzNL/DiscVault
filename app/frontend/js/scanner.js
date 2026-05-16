// ── Barcode Scanner ───────────────────────────────────────────────────────────
let _usingNative = false;
let _nativeStream = null;
let _nativeTimer = null;

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

async function doLookup(barcode) {
  showStatus('scanStatus', t('js.lookingUp'), 'info');
  document.getElementById('movieResult').style.display = 'none';
  document.getElementById('noResult').style.display = 'none';
  document.getElementById('tmdbCandidates').style.display = 'none';

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
          const icon = msg.status === 'searching' ? '<span class="spinner"></span>'
            : msg.status === 'hit' ? '✓' : msg.status === 'miss' ? '—' : '✕';
          showStatus('scanStatus', `${icon} ${msg.source}${msg.detail ? ': ' + msg.detail : ''}`, msg.status === 'hit' ? 'success' : 'info');
        } else if (msg.type === 'done') {
          finalData = msg;
        }
      }
    }

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

    if (finalData.status === 'exists') {
      showStatus('scanStatus', t('js.alreadyInCollection', finalData.movie.title), 'success');
      displayMovieResult(finalData.movie, barcode, true, finalData.detected_format);
    } else if (finalData.status === 'found') {
      showStatus('scanStatus', t('js.movieFound'), 'success');
      displayMovieResult(finalData.movie, barcode, false, finalData.detected_format);
      if (finalData.tmdb_candidates && finalData.tmdb_candidates.length > 1) {
        displayTmdbCandidates(finalData.tmdb_candidates, barcode);
      }
    } else {
      // Barcode not found: show empty placeholder so user can still supplement
      showStatus('scanStatus', t('js.movieNotFound', barcode), 'error');
      currentBarcode = barcode;
      currentMovieData = { title: finalData.raw_title || '', barcode };
      if (finalData.detected_format) currentMovieData.format = finalData.detected_format;
      document.getElementById('resultTitle').textContent = finalData.raw_title || barcode;
      const tags = document.getElementById('resultTags');
      tags.innerHTML = '';
      if (finalData.detected_format) tags.innerHTML += `<span class="tag format">${finalData.detected_format}</span>`;
      document.getElementById('resultPoster').innerHTML = '<div class="no-poster">🎬</div>';
      document.getElementById('movieResult').style.display = 'flex';
      document.getElementById('noResult').style.display = 'none';
      document.getElementById('btnSave').style.display = 'none';
    }
  } catch(e) {
    showStatus('scanStatus', t('js.connectionError', e.message), 'error');
    document.getElementById('noResult').style.display = 'block';
  }
}

function displayTmdbCandidates(candidates, barcode) {
  const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
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
      displayMovieResult(d.movie, barcode, false);
      showStatus('scanStatus', t('js.movieFound'), 'success');
    } else {
      showStatus('scanStatus', d.error || t('js.backendError', r.status), 'error');
    }
  } catch(e) {
    showStatus('scanStatus', t('js.connectionError', e.message), 'error');
  }
}

// Returns the best available poster URL for a movie object
function posterSrc(m) {
  if (m.poster_file) {
    const raw = String(m.poster_file).trim();
    if (raw) {
      if (/^https?:\/\//i.test(raw)) return raw;
      const fileName = raw.split(/[/\\]/).pop();
      if (fileName) return `/api/posters/${encodeURIComponent(fileName)}`;
    }
  }
  if (m.poster && m.poster !== 'N/A') return m.poster;
  return null;
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
  if (!alreadyInCollection) btnSave.innerHTML = '💾 Opslaan';
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

