(function () {
  const LOOKBACK_KEY = 'smartinbox-process-fetch-lookback';
  const UNIT_KEY = 'smartinbox-process-fetch-unit';
  const LEGACY_DAYS_KEY = 'smartinbox-process-fetch-days';

  const processStatus = document.getElementById('process-status');
  const processInboxes = document.getElementById('process-inboxes');
  const processLookback = document.getElementById('process-lookback');
  const processLookbackUnit = document.getElementById('process-lookback-unit');
  const btnFetch = document.getElementById('btn-fetch-mail');
  const btnReimport = document.getElementById('btn-reimport-mail');
  const btnRefresh = document.getElementById('btn-refresh-process');
  const processActivityLog = document.getElementById('process-activity-log');
  const btnClearActivityLog = document.getElementById('btn-clear-process-activity-log');

  let pollTimer = null;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function logSortKey(entry) {
    if (entry && entry.at != null) return Number(entry.at);
    return String(entry?.ts || '');
  }

  function compareLogsDesc(a, b) {
    const ka = logSortKey(a);
    const kb = logSortKey(b);
    if (typeof ka === 'number' && typeof kb === 'number') return kb - ka;
    return String(kb).localeCompare(String(ka));
  }

  function isHighlightedLog(msg) {
    return (
      msg.startsWith('Inbox check') ||
      msg.startsWith('Mail fetch') ||
      msg.startsWith('Mail refetch') ||
      msg.startsWith('New email (')
    );
  }

  function buildLogElement(entry) {
    const div = document.createElement('div');
    const lvl = (entry.level || 'info').replace('warning', 'warn');
    const msg = String(entry.message || '');
    const highlight = isHighlightedLog(msg);
    div.className = highlight ? 'activity-entry activity-entry--success' : 'activity-entry';
    const tsClass = highlight ? 'success' : lvl;
    div.innerHTML = `<span class="lvl-${tsClass}">[${entry.ts}]</span> ${escapeHtml(msg)}`;
    return div;
  }

  function renderActivityLog(entries) {
    if (!processActivityLog) return;
    processActivityLog.innerHTML = '';
    [...(entries || [])]
      .sort(compareLogsDesc)
      .slice(0, 120)
      .forEach((entry) => {
        processActivityLog.appendChild(buildLogElement(entry));
      });
  }

  function appendActivityLog(entry) {
    if (!processActivityLog || !entry) return;
    processActivityLog.prepend(buildLogElement(entry));
    while (processActivityLog.children.length > 120) {
      processActivityLog.removeChild(processActivityLog.lastChild);
    }
  }

  function connectActivityStream() {
    const es = new EventSource('/api/stream');
    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'snapshot') renderActivityLog(msg.data?.logs || []);
        if (msg.type === 'log') appendActivityLog(msg.data);
        if (msg.type === 'logs') renderActivityLog(msg.data || []);
      } catch (_) { /* ignore */ }
    };
  }

  function formatProviderCounts(counts) {
    if (!counts || typeof counts !== 'object') return '';
    const labels = { gmail: 'Gmail', proton: 'Proton', mail: 'Mail' };
    return Object.entries(counts)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, value]) => `${labels[key] || key}: ${value}`)
      .join(' · ');
  }

  function renderConnectedInboxes(accounts) {
    if (!processInboxes) return;
    const connected = (accounts || []).filter((a) => a.connected);
    if (!connected.length) {
      processInboxes.textContent =
        'No mail accounts connected — connect Gmail or Proton in Settings to fetch mail.';
      return;
    }
    processInboxes.textContent = connected
      .map((a) => `${a.label || a.provider}: ${a.email || 'connected'}`)
      .join(' · ');
  }

  function lookbackUnit() {
    const unit = String(processLookbackUnit?.value || 'days').toLowerCase();
    return unit === 'hours' ? 'hours' : 'days';
  }

  function lookbackLimits(unit) {
    if (unit === 'hours') {
      return { min: 1, max: 8760, defaultValue: 24 };
    }
    return { min: 1, max: 365, defaultValue: 5 };
  }

  function clampLookback(n, unit) {
    const limits = lookbackLimits(unit);
    if (!Number.isFinite(n)) return limits.defaultValue;
    return Math.max(limits.min, Math.min(limits.max, Math.round(n)));
  }

  function convertLookback(value, fromUnit, toUnit) {
    const from = fromUnit === 'hours' ? 'hours' : 'days';
    const to = toUnit === 'hours' ? 'hours' : 'days';
    if (from === to) return clampLookback(value, to);
    if (from === 'days' && to === 'hours') {
      return clampLookback(value * 24, 'hours');
    }
    return clampLookback(value / 24, 'days');
  }

  function fetchLookback() {
    const unit = lookbackUnit();
    const n = parseInt(processLookback?.value || String(lookbackLimits(unit).defaultValue), 10);
    return { value: clampLookback(n, unit), unit };
  }

  function lookbackLabel(value, unit) {
    if (unit === 'hours') {
      return `${value} hour${value === 1 ? '' : 's'}`;
    }
    return `${value} day${value === 1 ? '' : 's'}`;
  }

  function applyLookbackInputLimits(unit) {
    if (!processLookback) return;
    const limits = lookbackLimits(unit);
    processLookback.min = String(limits.min);
    processLookback.max = String(limits.max);
  }

  function setLookbackControls(value, unit) {
    const normalizedUnit = unit === 'hours' ? 'hours' : 'days';
    const normalizedValue = clampLookback(value, normalizedUnit);
    applyLookbackInputLimits(normalizedUnit);
    if (processLookbackUnit) processLookbackUnit.value = normalizedUnit;
    if (processLookback) processLookback.value = String(normalizedValue);
    return { value: normalizedValue, unit: normalizedUnit };
  }

  function saveLookback(value, unit) {
    try {
      localStorage.setItem(LOOKBACK_KEY, String(value));
      localStorage.setItem(UNIT_KEY, unit);
    } catch (_) { /* ignore */ }
  }

  function loadSavedLookback() {
    try {
      let unit = localStorage.getItem(UNIT_KEY) || 'days';
      unit = unit === 'hours' ? 'hours' : 'days';
      let raw = localStorage.getItem(LOOKBACK_KEY);
      if (raw == null) {
        raw = localStorage.getItem(LEGACY_DAYS_KEY);
        unit = 'days';
      }
      if (raw == null) return;
      setLookbackControls(parseInt(raw, 10), unit);
    } catch (_) { /* ignore */ }
  }

  function syncLookbackFromJob(job, running) {
    if (!job || running) return;
    const unit = job.unit === 'hours' ? 'hours' : 'days';
    const value =
      job.lookback ??
      (unit === 'hours' ? job.hours : job.days) ??
      lookbackLimits(unit).defaultValue;
    setLookbackControls(value, unit);
  }

  function phaseLabel(phase) {
    if (phase === 'delete') return 'Deleting local mail';
    if (phase === 'fetch') return 'Fetching from mail servers';
    if (phase === 'store') return 'Storing in database';
    return 'Working';
  }

  function renderStatus(data) {
    if (!processStatus) return;
    const parts = [];
    if (data.demo_mode) {
      parts.push('Demo mode — mail fetch disabled');
    }
    const job = data.fetch || {};
    const current = fetchLookback();
    const jobUnit = job.unit === 'hours' ? 'hours' : 'days';
    const jobValue =
      job.lookback ??
      (jobUnit === 'hours' ? job.hours : job.days) ??
      current.value;
    const windowLabel = lookbackLabel(jobValue, jobUnit);
    const providerNote = formatProviderCounts(job.by_provider);
    if (job.running) {
      const phase = phaseLabel(job.phase);
      const verb = job.replace ? 're-importing' : 're-fetching';
      let line = `${phase}: ${job.done || 0} / ${job.total || '…'} (${verb} last ${windowLabel})`;
      if (job.phase === 'delete' && (job.deleted || 0) > 0) {
        line = `${phase}: removed ${job.deleted} local message${job.deleted === 1 ? '' : 's'} (${verb} last ${windowLabel})`;
      }
      if (providerNote && job.phase === 'store') line += ` — ${providerNote}`;
      parts.push(`${line}…`);
    } else if ((job.total || 0) > 0 && (job.done || 0) >= (job.total || 0)) {
      const verb = job.replace ? 'Re-imported' : 'Fetched';
      let line = `${verb} ${job.total} message${job.total === 1 ? '' : 's'} (last ${windowLabel})`;
      if (job.replace && (job.deleted || 0) > 0) {
        line += ` — ${job.deleted} deleted locally first`;
      }
      if (providerNote) line += ` — ${providerNote}`;
      parts.push(line);
    } else if (!data.demo_mode) {
      const ready = fetchLookback();
      parts.push(
        `Ready — will re-fetch the last ${lookbackLabel(ready.value, ready.unit)} from connected inboxes`
      );
    }
    processStatus.textContent = parts.join(' · ');
  }

  function setBusy(busy, demoMode) {
    if (btnFetch) btnFetch.disabled = busy || !!demoMode;
    if (btnReimport) btnReimport.disabled = busy || !!demoMode;
    if (btnRefresh) btnRefresh.disabled = busy;
    if (processLookback) processLookback.disabled = busy || !!demoMode;
    if (processLookbackUnit) processLookbackUnit.disabled = busy || !!demoMode;
  }

  async function loadProcess() {
    try {
      const res = await fetch('/api/process');
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Failed to load process status');
      syncLookbackFromJob(data.fetch, data.fetch?.running);
      renderConnectedInboxes(data.mail_accounts);
      renderStatus(data);
      if (data.fetch?.running) {
        setBusy(true, data.demo_mode);
        schedulePoll();
      } else {
        setBusy(false, data.demo_mode);
        clearPoll();
      }
    } catch (e) {
      setBusy(false, false);
      if (processStatus) processStatus.textContent = `Error: ${e}`;
    }
  }

  async function fetchMail({ replace = false } = {}) {
    const lookback = fetchLookback();
    setLookbackControls(lookback.value, lookback.unit);
    saveLookback(lookback.value, lookback.unit);
    setBusy(true, false);
    const windowText = lookbackLabel(lookback.value, lookback.unit);
    if (processStatus) {
      processStatus.textContent = replace
        ? `Deleting local mail and re-importing last ${windowText}…`
        : `Starting mail re-fetch for last ${windowText}…`;
    }
    try {
      const res = await fetch('/api/process/fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lookback: lookback.value,
          unit: lookback.unit,
          replace: !!replace,
        }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Fetch failed');
      await loadProcess();
    } catch (e) {
      if (processStatus) processStatus.textContent = `Fetch error: ${e}`;
      setBusy(false, false);
    }
  }

  function schedulePoll() {
    clearPoll();
    pollTimer = window.setInterval(() => loadProcess(), 2000);
  }

  function clearPoll() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function updateReadyStatus() {
    if (processStatus && !btnFetch?.disabled) {
      const lookback = fetchLookback();
      processStatus.textContent =
        `Ready — will re-fetch the last ${lookbackLabel(lookback.value, lookback.unit)} from connected inboxes`;
    }
  }

  processLookback?.addEventListener('change', () => {
    const lookback = fetchLookback();
    setLookbackControls(lookback.value, lookback.unit);
    saveLookback(lookback.value, lookback.unit);
    updateReadyStatus();
  });

  processLookbackUnit?.addEventListener('change', () => {
    const nextUnit = lookbackUnit();
    const currentValue = parseInt(processLookback?.value || '5', 10);
    const converted = convertLookback(currentValue, nextUnit === 'hours' ? 'days' : 'hours', nextUnit);
    const lookback = setLookbackControls(converted, nextUnit);
    saveLookback(lookback.value, lookback.unit);
    updateReadyStatus();
  });

  processLookback?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !btnFetch?.disabled) {
      ev.preventDefault();
      fetchMail();
    }
  });

  btnRefresh?.addEventListener('click', () => loadProcess());
  btnFetch?.addEventListener('click', () => fetchMail());
  btnReimport?.addEventListener('click', async () => {
    const lookback = fetchLookback();
    const windowText = lookbackLabel(lookback.value, lookback.unit);
    const ok = window.confirm(
      `Delete all local mail from the last ${windowText} and re-import from your inboxes?\n\n` +
        'Summaries, spam checks, and calendar extraction will run again for imported messages. ' +
        'This cannot be undone.'
    );
    if (!ok) return;
    await fetchMail({ replace: true });
  });
  btnClearActivityLog?.addEventListener('click', async () => {
    try {
      await fetch('/api/logs/clear', { method: 'POST' });
      if (processActivityLog) processActivityLog.innerHTML = '';
    } catch (_) { /* ignore */ }
  });

  applyLookbackInputLimits('days');
  loadSavedLookback();
  connectActivityStream();
  loadProcess();
})();