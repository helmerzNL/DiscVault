// ── Barcode Scanner ───────────────────────────────────────────────────────────
function startScanner() {
  const container = document.getElementById('scanner-container');
  document.getElementById('scannerPlaceholder').style.display = 'none';
  document.getElementById('btnStartScan').style.display = 'none';
  document.getElementById('btnStopScan').style.display = 'inline-flex';

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
    Quagga.start();
    scannerRunning = true;

    // Add scan line animation
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
  if (scannerRunning) {
    Quagga.stop();
    scannerRunning = false;
  }
  document.getElementById('btnStartScan').style.display = 'inline-flex';
  document.getElementById('btnStopScan').style.display = 'none';
  // Re-show placeholder
  const container = document.getElementById('scanner-container');
  const overlay = container.querySelector('.scanner-overlay');
  if (overlay) overlay.remove();
  document.getElementById('scannerPlaceholder').style.display = 'flex';
  document.getElementById('scannerPlaceholder').style.flexDirection = 'column';
  document.getElementById('scannerPlaceholder').style.alignItems = 'center';
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

