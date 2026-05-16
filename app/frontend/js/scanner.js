// ── Barcode Scanner ───────────────────────────────────────────────────────────
let _usingNative = false;
let _nativeStream = null;
let _nativeTimer = null;

function _supportsNativeDetector() {
  return typeof BarcodeDetector !== 'undefined';
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
    detector = new BarcodeDetector({ formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e'] });
  } catch(e) {
    return 'fallback';
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
  } catch(e) {
    showStatus('scanStatus', t('js.cameraError', e.message), 'error');
    _resetScannerUI();
    return 'error';
  }

  _usingNative = true;
  _nativeStream = stream;

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

  async function runDetect() {
    if (!scannerRunning || !_usingNative) return;
    try {
      const results = await detector.detect(video);
      for (const barcode of results) {
        const code = barcode.rawValue;
        const now = Date.now();
        if (code === lastCode && now - lastTime < 3000) continue;
        lastCode = code;
        lastTime = now;
        stopScanner();
        document.getElementById('manualBarcode').value = code;
        doLookup(code);
        return;
      }
    } catch(e) { /* video not ready yet */ }
    if (scannerRunning && _usingNative) _nativeTimer = setTimeout(runDetect, 150);
  }
  _nativeTimer = setTimeout(runDetect, 200);
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
      constraints: { facingMode: 'environment' }
    },
    decoder: {
      readers: ['ean_reader', 'upc_reader', 'upc_e_reader', 'ean_8_reader']
    },
    locate: true
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
  });

  let lastCode = '';
  let lastTime = 0;
  Quagga.onDetected(result => {
    const code = result.codeResult.code;
    const now = Date.now();
    if (code === lastCode && now - lastTime < 3000) return;
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
  document.getElementById('movieResult').classList.remove('visible');
  document.getElementById('noResult').style.display = 'none';
  document.getElementById('tmdbCandidates').style.display = 'none';

  try {
    const r = await fetch(`${API}/lookup/${barcode}?stream=1`);
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
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
      displayMovieResult(finalData.movie, barcode, true);
    } else if (finalData.status === 'found') {
      showStatus('scanStatus', t('js.movieFound'), 'success');
      displayMovieResult(finalData.movie, barcode, false);
      if (finalData.tmdb_candidates && finalData.tmdb_candidates.length > 1) {
        displayTmdbCandidates(finalData.tmdb_candidates, barcode);
      }
    } else {
      showStatus('scanStatus', t('js.movieNotFound', barcode), 'error');
      currentBarcode = barcode;
      currentMovieData = { title: finalData.raw_title || '', barcode };
      document.getElementById('movieResult').classList.add('visible');
      document.getElementById('resultTitle').textContent = finalData.raw_title || t('js.unknown');
      document.getElementById('resultPlot').textContent = '';
      document.getElementById('resultTags').innerHTML = '';
      const poster = document.getElementById('resultPoster');
      poster.innerHTML = '<div class="no-poster">🎬</div>';
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

function displayMovieResult(movie, barcode, alreadyInCollection) {
  currentBarcode = barcode;
  currentMovieData = { ...movie, barcode };

  document.getElementById('resultTitle').textContent = movie.title || '—';
  document.getElementById('resultPlot').textContent = movie.plot || '';

  const tags = document.getElementById('resultTags');
  tags.innerHTML = '';
  if (movie.year) tags.innerHTML += `<span class="tag">${movie.year}</span>`;
  if (movie.director) tags.innerHTML += `<span class="tag">${movie.director}</span>`;
  if (movie.genre) movie.genre.split(',').forEach(g => { tags.innerHTML += `<span class="tag">${g.trim()}</span>`; });
  if (movie.rating) tags.innerHTML += `<span class="tag">⭐ ${movie.rating}</span>`;
  if (movie.runtime) tags.innerHTML += `<span class="tag">${movie.runtime}</span>`;

  const src = posterSrc(movie);
  const poster = document.getElementById('resultPoster');
  if (src) {
    poster.innerHTML = `<img src="${src}" onerror="this.parentElement.innerHTML='<div class=\\'no-poster\\'>🎬</div>'">`;
  } else {
    poster.innerHTML = '<div class="no-poster">🎬</div>';
  }

  document.getElementById('movieResult').classList.add('visible');
  document.getElementById('noResult').style.display = 'none';
  document.getElementById('btnSave').style.display = alreadyInCollection ? 'none' : 'inline-flex';
  document.getElementById('resultHdr').value = movie.hdr || '';
  document.getElementById('resultAudioTracks').value = movie.audio_tracks || '';
  document.getElementById('resultSubtitles').value = movie.subtitles || '';

  if (alreadyInCollection) {
    document.getElementById('btnSave').textContent = t('js.alreadyInCollectionBtn');
  }
}

async function saveMovie() {
  const btn = document.getElementById('btnSave');
  btn.innerHTML = t('js.saving');
  btn.disabled = true;

  const payload = {
    ...currentMovieData,
    barcode: currentBarcode,
    format: document.getElementById('resultFormat').value,
    location: document.getElementById('resultLocation').value,
    hdr: document.getElementById('resultHdr').value,
    audio_tracks: document.getElementById('resultAudioTracks').value,
    subtitles: document.getElementById('resultSubtitles').value,
  };

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
        btn.style.display = 'none';
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

