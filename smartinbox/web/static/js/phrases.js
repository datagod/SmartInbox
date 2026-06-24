(function () {
  const phrasesStatus = document.getElementById('phrases-status');
  const phrasesTbody = document.getElementById('phrases-tbody');
  const recordingsStatus = document.getElementById('recordings-status');
  const recordingsTbody = document.getElementById('recordings-tbody');
  const phraseModeFilter = document.getElementById('phrase-mode-filter');
  const phraseStatusFilter = document.getElementById('phrase-status-filter');
  const phrasesSelectAll = document.getElementById('phrases-select-all');
  const recordingsSelectAll = document.getElementById('recordings-select-all');
  const btnDeleteSelected = document.getElementById('btn-delete-selected');
  const btnDeleteRecordings = document.getElementById('btn-delete-recordings');
  const player = document.getElementById('phrases-player');

  let phrasesCache = [];
  let recordingsCache = [];
  let phrasesSelected = new Set();
  let recordingsSelected = new Set();
  let playingFilename = null;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatBytes(n) {
    const size = Number(n) || 0;
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatTime(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString();
  }

  function recordingUrl(filename, download) {
    const q = download ? '?download=true' : '';
    return `/api/recordings/${encodeURIComponent(filename)}${q}`;
  }

  function setPlayState(filename, playing) {
    document.querySelectorAll('[data-recording-play]').forEach((btn) => {
      const name = btn.getAttribute('data-recording-play');
      btn.textContent = playing && name === filename ? '■ Stop' : '▶ Play';
      btn.classList.toggle('is-playing', playing && name === filename);
    });
  }

  function stopPlayback() {
    if (!player) return;
    player.pause();
    player.removeAttribute('src');
    setPlayState(playingFilename, false);
    playingFilename = null;
  }

  async function playRecording(filename) {
    if (!player || !filename) return;
    if (playingFilename === filename && !player.paused) {
      stopPlayback();
      return;
    }
    stopPlayback();
    playingFilename = filename;
    player.src = recordingUrl(filename, false);
    setPlayState(filename, true);
    try {
      await player.play();
    } catch (e) {
      stopPlayback();
      if (phrasesStatus) phrasesStatus.textContent = `Playback failed: ${e}`;
    }
  }

  function filteredPhrases() {
    const mode = phraseModeFilter ? phraseModeFilter.value : '';
    const status = phraseStatusFilter ? phraseStatusFilter.value : 'all';
    return phrasesCache.filter((row) => {
      if (mode && row.mode !== mode) return false;
      if (status === 'recorded' && !row.recorded) return false;
      if (status === 'missing' && row.recorded) return false;
      return true;
    });
  }

  function updatePhraseSelectionUi() {
    const visible = filteredPhrases();
    const n = phrasesSelected.size;
    if (btnDeleteSelected) {
      btnDeleteSelected.disabled = n === 0;
    }
    if (phrasesSelectAll) {
      const allOnPage = visible.length > 0 && visible.every((r) => phrasesSelected.has(r.text));
      const some = visible.some((r) => phrasesSelected.has(r.text));
      phrasesSelectAll.checked = allOnPage;
      phrasesSelectAll.indeterminate = some && !allOnPage;
    }
  }

  function updateRecordingsSelectionUi() {
    const n = recordingsSelected.size;
    if (btnDeleteRecordings) {
      btnDeleteRecordings.disabled = n === 0;
    }
    if (recordingsSelectAll) {
      const allOnPage =
        recordingsCache.length > 0 &&
        recordingsCache.every((r) => recordingsSelected.has(r.filename));
      const some = recordingsCache.some((r) => recordingsSelected.has(r.filename));
      recordingsSelectAll.checked = allOnPage;
      recordingsSelectAll.indeterminate = some && !allOnPage;
    }
  }

  function actionButtons(filename, text, mode) {
    const fileAttr = escapeHtml(filename || '');
    const textAttr = escapeHtml(text || '');
    const modeAttr = escapeHtml(mode || '');
    const playBtn = filename
      ? `<button type="button" class="btn btn-secondary btn-small" data-recording-play="${fileAttr}" data-file="${fileAttr}">▶ Play</button>`
      : `<button type="button" class="btn btn-secondary btn-small" data-generate-phrase="${textAttr}" data-mode="${modeAttr}">Generate</button>`;
    const dlBtn = filename
      ? `<a class="btn btn-secondary btn-small" href="${recordingUrl(filename, true)}" download>↓ Save</a>`
      : '';
    const delBtn = filename
      ? `<button type="button" class="btn btn-secondary btn-small btn-danger" data-delete-file="${fileAttr}">Delete</button>`
      : '';
    return `<div class="phrases-row-actions">${playBtn}${dlBtn}${delBtn}</div>`;
  }

  function renderPhrases() {
    const rows = filteredPhrases();
    if (!phrasesTbody) return;
    if (!rows.length) {
      phrasesTbody.innerHTML =
        '<tr><td colspan="6" class="phrases-empty">No phrases match the current filters.</td></tr>';
      updatePhraseSelectionUi();
      return;
    }
    phrasesTbody.innerHTML = rows
      .map((row) => {
        const checked = phrasesSelected.has(row.text) ? ' checked' : '';
        const status = row.recorded
          ? `<span class="phrase-badge recorded">Recorded</span>`
          : `<span class="phrase-badge missing">Not generated</span>`;
        const spoken =
          row.spoken_text && row.spoken_text !== row.text
            ? `<div class="phrase-spoken">${escapeHtml(row.spoken_text)}</div>`
            : '';
        return `<tr>
          <td class="col-check"><input type="checkbox" data-phrase-select="${escapeHtml(row.text)}"${checked} /></td>
          <td class="col-mode"><span class="mode-pill mode-${escapeHtml(row.mode)}">${escapeHtml(row.mode_label || row.mode)}</span></td>
          <td class="col-phrase"><div class="phrase-text">${escapeHtml(row.text)}</div>${spoken}</td>
          <td class="col-status">${status}</td>
          <td class="col-size">${row.recorded ? formatBytes(row.size_bytes) : '—'}</td>
          <td class="col-actions">${actionButtons(row.filename, row.text, row.mode)}</td>
        </tr>`;
      })
      .join('');
    if (playingFilename) setPlayState(playingFilename, true);
    updatePhraseSelectionUi();
  }

  function renderRecordings() {
    if (!recordingsTbody) return;
    if (!recordingsCache.length) {
      recordingsTbody.innerHTML =
        '<tr><td colspan="6" class="phrases-empty">No alert recordings yet. Voice alerts save audio here when new mail arrives.</td></tr>';
      updateRecordingsSelectionUi();
      return;
    }
    recordingsTbody.innerHTML = recordingsCache
      .map((row) => {
        const file = row.filename || '';
        const checked = recordingsSelected.has(file) ? ' checked' : '';
        const mode = row.delivery_mode
          ? `<span class="mode-pill mode-${escapeHtml(row.delivery_mode)}">${escapeHtml(row.delivery_mode)}</span>`
          : '—';
        return `<tr>
          <td class="col-check"><input type="checkbox" data-recording-select="${escapeHtml(file)}"${checked} /></td>
          <td class="col-time">${formatTime(row.modified_at)}</td>
          <td class="col-phrase"><div class="phrase-text">${escapeHtml(row.message || file)}</div></td>
          <td class="col-mode">${mode}</td>
          <td class="col-size">${formatBytes(row.size_bytes)}</td>
          <td class="col-actions">${actionButtons(file, '', '')}</td>
        </tr>`;
      })
      .join('');
    if (playingFilename) setPlayState(playingFilename, true);
    updateRecordingsSelectionUi();
  }

  async function loadPhrases() {
    if (phrasesStatus) phrasesStatus.textContent = 'Loading phrase catalog…';
    try {
      const res = await fetch('/api/phrases');
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Failed to load phrases');
      phrasesCache = data.phrases || [];
      const recorded = data.recorded || 0;
      const total = data.total || phrasesCache.length;
      if (phrasesStatus) {
        phrasesStatus.textContent = `${recorded} of ${total} phrases recorded in ${data.phrases_directory || 'localrecordings/phrases/'}`;
      }
      renderPhrases();
    } catch (e) {
      if (phrasesStatus) phrasesStatus.textContent = `Error: ${e}`;
      if (phrasesTbody) {
        phrasesTbody.innerHTML =
          '<tr><td colspan="6" class="phrases-empty">Could not load phrases.</td></tr>';
      }
    }
  }

  async function loadRecordings() {
    if (recordingsStatus) recordingsStatus.textContent = 'Loading…';
    try {
      const res = await fetch('/api/recordings');
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Failed to load recordings');
      recordingsCache = (data.recordings || []).filter((row) => row.kind !== 'phrase');
      if (recordingsStatus) {
        recordingsStatus.textContent = `${recordingsCache.length} alert recording(s) in ${data.directory || 'localrecordings/'}`;
      }
      const known = new Set(recordingsCache.map((r) => r.filename));
      Array.from(recordingsSelected).forEach((f) => {
        if (!known.has(f)) recordingsSelected.delete(f);
      });
      renderRecordings();
    } catch (e) {
      if (recordingsStatus) recordingsStatus.textContent = `Error: ${e}`;
      if (recordingsTbody) {
        recordingsTbody.innerHTML =
          '<tr><td colspan="6" class="phrases-empty">Could not load recordings.</td></tr>';
      }
    }
  }

  async function generatePhrases(options) {
    if (phrasesStatus) phrasesStatus.textContent = 'Generating phrases via Chatterbox…';
    const btnMissing = document.getElementById('btn-generate-missing');
    const btnAll = document.getElementById('btn-generate-all');
    if (btnMissing) btnMissing.disabled = true;
    if (btnAll) btnAll.disabled = true;
    try {
      const res = await fetch('/api/phrases/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(options),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Generation failed');
      const made = (data.generated || []).length;
      const skipped = (data.skipped || []).length;
      const errCount = Object.keys(data.errors || {}).length;
      if (phrasesStatus) {
        phrasesStatus.textContent = `Generated ${made}, skipped ${skipped}${errCount ? `, ${errCount} error(s)` : ''}.`;
      }
      await Promise.all([loadPhrases(), loadRecordings()]);
    } catch (e) {
      if (phrasesStatus) phrasesStatus.textContent = `Generation failed: ${e}`;
    } finally {
      if (btnMissing) btnMissing.disabled = false;
      if (btnAll) btnAll.disabled = false;
    }
  }

  async function deleteFiles(names) {
    const res = await fetch('/api/recordings/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filenames: names }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Delete failed');
    return data;
  }

  document.getElementById('btn-refresh-phrases')?.addEventListener('click', () => loadPhrases());
  document.getElementById('btn-refresh-recordings')?.addEventListener('click', () => loadRecordings());

  document.getElementById('btn-generate-missing')?.addEventListener('click', () => {
    generatePhrases({
      missing_only: true,
      mode: phraseModeFilter?.value || undefined,
    });
  });

  document.getElementById('btn-generate-all')?.addEventListener('click', () => {
    if (!confirm('Regenerate all matching phrases? Existing files will be replaced.')) return;
    generatePhrases({
      missing_only: false,
      mode: phraseModeFilter?.value || undefined,
    });
  });

  phraseModeFilter?.addEventListener('change', renderPhrases);
  phraseStatusFilter?.addEventListener('change', renderPhrases);

  phrasesSelectAll?.addEventListener('change', (e) => {
    const checked = e.target.checked;
    filteredPhrases().forEach((row) => {
      if (checked) phrasesSelected.add(row.text);
      else phrasesSelected.delete(row.text);
    });
    document.querySelectorAll('[data-phrase-select]').forEach((cb) => {
      cb.checked = checked;
    });
    updatePhraseSelectionUi();
  });

  recordingsSelectAll?.addEventListener('change', (e) => {
    const checked = e.target.checked;
    recordingsCache.forEach((row) => {
      if (checked) recordingsSelected.add(row.filename);
      else recordingsSelected.delete(row.filename);
    });
    document.querySelectorAll('[data-recording-select]').forEach((cb) => {
      cb.checked = checked;
    });
    updateRecordingsSelectionUi();
  });

  btnDeleteSelected?.addEventListener('click', async () => {
    const files = filteredPhrases()
      .filter((row) => phrasesSelected.has(row.text) && row.filename)
      .map((row) => row.filename);
    if (!files.length) return;
    if (!confirm(`Delete ${files.length} phrase recording(s)?`)) return;
    if (playingFilename && files.includes(playingFilename)) stopPlayback();
    try {
      await deleteFiles(files);
      files.forEach((f) => {
        recordingsSelected.delete(f);
      });
      phrasesSelected.clear();
      await Promise.all([loadPhrases(), loadRecordings()]);
    } catch (e) {
      if (phrasesStatus) phrasesStatus.textContent = `Delete failed: ${e}`;
    }
  });

  btnDeleteRecordings?.addEventListener('click', async () => {
    const files = Array.from(recordingsSelected);
    if (!files.length) return;
    if (!confirm(`Delete ${files.length} alert recording(s)?`)) return;
    if (playingFilename && files.includes(playingFilename)) stopPlayback();
    try {
      await deleteFiles(files);
      recordingsSelected.clear();
      await loadRecordings();
    } catch (e) {
      if (recordingsStatus) recordingsStatus.textContent = `Delete failed: ${e}`;
    }
  });

  document.body.addEventListener('click', async (e) => {
    const playBtn = e.target.closest('[data-recording-play]');
    if (playBtn) {
      await playRecording(playBtn.dataset.file);
      return;
    }
    const genBtn = e.target.closest('[data-generate-phrase]');
    if (genBtn) {
      await generatePhrases({
        text: genBtn.dataset.generatePhrase,
        mode: genBtn.dataset.mode,
        missing_only: false,
      });
      return;
    }
    const delBtn = e.target.closest('[data-delete-file]');
    if (delBtn) {
      const file = delBtn.dataset.deleteFile;
      if (!confirm(`Delete ${file}?`)) return;
      if (playingFilename === file) stopPlayback();
      try {
        await deleteFiles([file]);
        await Promise.all([loadPhrases(), loadRecordings()]);
      } catch (err) {
        if (phrasesStatus) phrasesStatus.textContent = `Delete failed: ${err}`;
      }
    }
  });

  document.body.addEventListener('change', (e) => {
    const phraseCb = e.target.closest('[data-phrase-select]');
    if (phraseCb) {
      const text = phraseCb.getAttribute('data-phrase-select');
      if (phraseCb.checked) phrasesSelected.add(text);
      else phrasesSelected.delete(text);
      updatePhraseSelectionUi();
      return;
    }
    const recCb = e.target.closest('[data-recording-select]');
    if (recCb) {
      const file = recCb.getAttribute('data-recording-select');
      if (recCb.checked) recordingsSelected.add(file);
      else recordingsSelected.delete(file);
      updateRecordingsSelectionUi();
    }
  });

  if (player) {
    player.addEventListener('ended', stopPlayback);
    player.addEventListener('pause', () => {
      if (player.ended || player.currentTime === 0) return;
    });
  }

  Promise.all([loadPhrases(), loadRecordings()]);
})();