(function () {
  const LOOKBACK_KEY = 'smartinbox-process-fetch-lookback';
  const UNIT_KEY = 'smartinbox-process-fetch-unit';
  const LEGACY_DAYS_KEY = 'smartinbox-process-fetch-days';
  const CAL_LOOKBACK_KEY = 'smartinbox-calendar-lookback';
  const CAL_UNIT_KEY = 'smartinbox-calendar-lookback-unit';
  const CAL_LEGACY_DAYS_KEY = 'smartinbox-calendar-days';
  const SUM_LOOKBACK_KEY = 'smartinbox-summary-lookback';
  const SUM_UNIT_KEY = 'smartinbox-summary-lookback-unit';

  const processStatus = document.getElementById('process-status');
  const processInboxes = document.getElementById('process-inboxes');
  const processLookback = document.getElementById('process-lookback');
  const processLookbackUnit = document.getElementById('process-lookback-unit');
  const btnFetch = document.getElementById('btn-fetch-mail');
  const btnReimport = document.getElementById('btn-reimport-mail');
  const btnRefresh = document.getElementById('btn-refresh-process');
  const processActivityLog = document.getElementById('process-activity-log');
  const btnClearActivityLog = document.getElementById('btn-clear-process-activity-log');
  const pipelineStats = document.getElementById('process-pipeline-stats');
  const calendarQueueEl = document.getElementById('process-calendar-queue');
  const calendarCount = document.getElementById('process-calendar-count');
  const calendarSparkFill = document.getElementById('process-calendar-spark-fill');
  const calendarSparkPath = document.getElementById('process-calendar-spark-path');
  const calendarNote = document.getElementById('process-calendar-note');
  const summaryQueueEl = document.getElementById('process-summary-queue');
  const summaryCount = document.getElementById('process-summary-count');
  const summarySparkFill = document.getElementById('process-summary-spark-fill');
  const summarySparkPath = document.getElementById('process-summary-spark-path');
  const summaryNote = document.getElementById('process-summary-note');
  const llmTimings = document.getElementById('process-llm-timings');
  const pipelineDetails = document.getElementById('process-pipeline-details');
  const calendarLookback = document.getElementById('process-calendar-lookback');
  const calendarLookbackUnitSelect = document.getElementById('process-calendar-lookback-unit');
  const summaryLookback = document.getElementById('process-summary-lookback');
  const summaryLookbackUnitSelect = document.getElementById('process-summary-lookback-unit');
  const btnSummaryClearQueue = document.getElementById('btn-summary-clear-queue');
  const btnSummaryReprocess = document.getElementById('btn-summary-reprocess');
  const processSummaryStatus = document.getElementById('process-summary-status');
  const btnCalendarClearQueue = document.getElementById('btn-calendar-clear-queue');
  const btnCalendarReprocess = document.getElementById('btn-calendar-reprocess');
  const processCalendarStatus = document.getElementById('process-calendar-status');
  const btnKickstartPipelines = document.getElementById('btn-kickstart-pipelines');
  const processKickstartStatus = document.getElementById('process-kickstart-status');
  const btnInvestigatePipelines = document.getElementById('btn-investigate-pipelines');
  const btnRestartChatterbox = document.getElementById('btn-restart-chatterbox');
  const processPipelineInvestigateStatus = document.getElementById(
    'process-pipeline-investigate-status'
  );
  const processPipelineInvestigateResults = document.getElementById(
    'process-pipeline-investigate-results'
  );
  const processTtsInvestigateStatus = document.getElementById('process-tts-investigate-status');
  const processTtsInvestigateResults = document.getElementById('process-tts-investigate-results');

  let pollTimer = null;
  let pipelinePollTimer = null;

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
      msg.startsWith('New email (') ||
      msg.startsWith('Calendar') ||
      msg.startsWith('Summary')
    );
  }

  function localLogEntry(message, level = 'info') {
    const now = new Date();
    return {
      ts: now.toLocaleTimeString('en-US', { hour12: false }),
      at: Date.now() / 1000,
      level,
      message: String(message || ''),
    };
  }

  function isMiddlemanLog(entry) {
    const lvl = String(entry?.level || '').toLowerCase();
    if (lvl === 'middleman') return true;
    const msg = String(entry?.message || '');
    return (
      /middleman/i.test(msg) ||
      /third-party recruiter/i.test(msg) ||
      /Suspected third-party/i.test(msg)
    );
  }

  function buildLogElement(entry) {
    const div = document.createElement('div');
    const lvl = (entry.level || 'info').replace('warning', 'warn');
    const msg = String(entry.message || '');
    const highlight = isHighlightedLog(msg);
    const middleman = isMiddlemanLog(entry);
    let cls = 'activity-entry';
    if (highlight) cls += ' activity-entry--success';
    if (middleman) cls += ' activity-entry--middleman';
    div.className = cls;
    const tsClass = highlight ? 'success' : middleman ? 'middleman' : lvl;
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

  function formatSec(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return `${n.toFixed(n >= 10 ? 0 : 1)}s`;
  }

  function formatAgo(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return 'never';
    const delta = Math.max(0, Math.round(Date.now() / 1000 - n));
    if (delta < 60) return `${delta}s ago`;
    if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
    return `${Math.round(delta / 3600)}h ago`;
  }

  function formatGpu(gpu) {
    if (gpu == null || gpu === '') return 'auto';
    return `GPU ${gpu}`;
  }

  function dlItem(label, value) {
    return `<div class="storage-dl-row"><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`;
  }

  function statCard(value, label, extraClass = '') {
    return `
      <div class="storage-stat-card ${extraClass}">
        <span class="storage-stat-value">${escapeHtml(String(value))}</span>
        <span class="storage-stat-label">${escapeHtml(label)}</span>
      </div>`;
  }

  function buildSparklinePaths(values) {
    const width = 120;
    const height = 28;
    const pad = 2;
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const range = Math.max(max - min, 1);
    const points = values.map((value, index) => {
      const x = pad + (index / Math.max(values.length - 1, 1)) * (width - pad * 2);
      const y = pad + (1 - (value - min) / range) * (height - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    const linePath = `M ${points.join(' L ')}`;
    const fillPath = `${linePath} L ${width - pad},${height - pad} L ${pad},${height - pad} Z`;
    return { linePath, fillPath, max };
  }

  function renderSparkline({
    root,
    countEl,
    fillEl,
    pathEl,
    noteEl,
    queue,
    valueKey,
    highThreshold,
    noteBuilder,
  }) {
    if (!root || !queue) return;
    const current = Number(queue[valueKey]) || 0;
    if (countEl) countEl.textContent = String(current);

    const history = Array.isArray(queue.history) ? queue.history : [];
    const values = history.map((point) => Number(point[valueKey]) || 0);
    if (!values.length) values.push(current);

    const { linePath, fillPath } = buildSparklinePaths(values);
    if (pathEl) pathEl.setAttribute('d', linePath);
    if (fillEl) fillEl.setAttribute('d', fillPath);

    root.classList.toggle('is-high', current >= highThreshold);
    root.classList.toggle('is-clear', current === 0);

    if (noteEl && noteBuilder) {
      noteEl.textContent = noteBuilder(queue, current);
    }
  }

  function renderPipelineStats(pipeline) {
    if (!pipelineStats) return;
    const queue = pipeline?.queue || {};
    const pending = Number(queue.pending) || 0;
    const summaryPending = Number(queue.summary_pending) || 0;
    const inFlight = Number(pipeline?.summary_in_flight) || 0;
    const tasks = Number(pipeline?.email_tasks_active) || 0;
    const calBf = pipeline?.calendar_backfill || {};
    const sumBf = pipeline?.summary_backfill || {};
    const calScan = calBf.running
      ? `${calBf.done || 0}/${calBf.total || '…'}`
      : 'idle';
    const sumScan = sumBf.running
      ? `${sumBf.done || 0}/${sumBf.total || '…'}`
      : (pipeline?.summary_backfill_active ? 'running' : 'idle');

    pipelineStats.innerHTML = [
      statCard(pending.toLocaleString(), 'Calendar pending'),
      statCard(summaryPending.toLocaleString(), 'Awaiting summary'),
      statCard(inFlight, 'Summaries in flight'),
      statCard(tasks, 'Active tasks'),
      statCard(
        sumScan,
        'Summary backfill',
        (sumBf.running || pipeline?.summary_backfill_active) ? 'is-active' : ''
      ),
      statCard(calScan, 'Calendar scan'),
    ].join('');
  }

  function renderPipelineSparklines(pipeline) {
    const queue = pipeline?.queue || {};
    const days = queue.list_days || 30;

    renderSparkline({
      root: calendarQueueEl,
      countEl: calendarCount,
      fillEl: calendarSparkFill,
      pathEl: calendarSparkPath,
      noteEl: calendarNote,
      queue,
      valueKey: 'pending',
      highThreshold: 25,
      noteBuilder: (q) => {
        const notes = [`last ${days} day${days === 1 ? '' : 's'} of stored mail`];
        if (q.calendar_scan_running && q.scan_total > 0) {
          notes.push(`scanning ${q.scan_done || 0}/${q.scan_total}`);
        } else if (q.blocked_by_summary && q.summary_pending > 0) {
          notes.push('LLM extraction waits for summaries');
        } else if ((q.pending || 0) === 0 && (q.scanned_no_events || 0) > 0) {
          notes.push(`idle — ${q.scanned_no_events} scanned with no dates`);
        } else if ((q.pending || 0) === 0 && !q.calendar_scan_running) {
          notes.push('idle — queue clear');
        }
        return notes.join(' · ');
      },
    });

    renderSparkline({
      root: summaryQueueEl,
      countEl: summaryCount,
      fillEl: summarySparkFill,
      pathEl: summarySparkPath,
      noteEl: summaryNote,
      queue,
      valueKey: 'summary_pending',
      highThreshold: 50,
      noteBuilder: (q, current) => {
        const notes = [];
        const sumBf = pipeline?.summary_backfill || {};
        if (sumBf.running && sumBf.total > 0) {
          notes.push(`backfill ${sumBf.done || 0}/${sumBf.total}`);
        } else if (pipeline?.summary_backfill_active) {
          notes.push('backfill running');
        }
        if (pipeline?.summary_in_flight > 0) {
          notes.push(`${pipeline.summary_in_flight} in flight`);
        }
        if (!notes.length && current > 0) notes.push('summaries prioritized over calendar LLM');
        if (!notes.length) notes.push('queue clear');
        if (q.blocked_by_summary && current > 0) notes.push('calendar LLM waiting');
        return notes.join(' · ');
      },
    });
  }

  function renderLlmTimings(pipeline) {
    if (!llmTimings) return;
    const ollama = pipeline?.ollama || {};
    const timings = pipeline?.llm_timings || {};
    const cards = [
      { key: 'summary', label: 'Summary', model: ollama.model, slow: 45 },
      { key: 'spam', label: 'Spam check', model: (pipeline?.spam || {}).engine || 'SpamAssassin', slow: 5 },
      { key: 'calendar', label: 'Calendar LLM', model: ollama.model, slow: 60 },
    ];

    llmTimings.innerHTML = cards
      .map(({ key, label, model, slow }) => {
        const t = timings[key] || {};
        const avg = formatSec(t.avg_sec);
        const last = formatSec(t.last_sec);
        const count = Number(t.count) || 0;
        const lastOk = t.last_success !== false;
        const isSlow = Number(t.last_sec) >= slow;
        const cls = [
          'process-llm-card',
          isSlow ? 'is-slow' : '',
          count > 0 && !lastOk ? 'is-fail' : '',
        ]
          .filter(Boolean)
          .join(' ');
        return `
          <div class="${cls}">
            <div class="process-llm-card-head">
              <span class="process-llm-card-label">${escapeHtml(label)}</span>
              <span class="process-llm-card-model" title="${escapeHtml(model || '')}">${escapeHtml(model || '—')}</span>
            </div>
            <dl class="process-llm-card-metrics">
              <div class="process-llm-metric">
                <dt>Avg</dt><dd>${escapeHtml(avg)}</dd>
              </div>
              <div class="process-llm-metric">
                <dt>Last</dt><dd>${escapeHtml(last)}</dd>
              </div>
              <div class="process-llm-metric">
                <dt>Calls</dt><dd>${count}</dd>
              </div>
              <div class="process-llm-metric">
                <dt>Last call</dt><dd>${escapeHtml(formatAgo(t.last_at))}</dd>
              </div>
            </dl>
          </div>`;
      })
      .join('');
  }

  function calendarLookbackUnit() {
    const unit = String(calendarLookbackUnitSelect?.value || 'days').toLowerCase();
    return unit === 'hours' ? 'hours' : 'days';
  }

  function calendarLookbackLimits(unit) {
    if (unit === 'hours') {
      return { min: 1, max: 8760, defaultValue: 24 };
    }
    return { min: 1, max: 365, defaultValue: 5 };
  }

  function clampCalendarLookback(n, unit) {
    const limits = calendarLookbackLimits(unit);
    if (!Number.isFinite(n)) return limits.defaultValue;
    return Math.max(limits.min, Math.min(limits.max, Math.round(n)));
  }

  function convertCalendarLookback(value, fromUnit, toUnit) {
    const from = fromUnit === 'hours' ? 'hours' : 'days';
    const to = toUnit === 'hours' ? 'hours' : 'days';
    if (from === to) return clampCalendarLookback(value, to);
    if (from === 'days' && to === 'hours') {
      return clampCalendarLookback(value * 24, 'hours');
    }
    return clampCalendarLookback(value / 24, 'days');
  }

  function calendarReprocessLookback() {
    const unit = calendarLookbackUnit();
    const n = parseInt(
      calendarLookback?.value || String(calendarLookbackLimits(unit).defaultValue),
      10
    );
    return { value: clampCalendarLookback(n, unit), unit };
  }

  function applyCalendarLookbackInputLimits(unit) {
    if (!calendarLookback) return;
    const limits = calendarLookbackLimits(unit);
    calendarLookback.min = String(limits.min);
    calendarLookback.max = String(limits.max);
  }

  function setCalendarLookbackControls(value, unit) {
    const normalizedUnit = unit === 'hours' ? 'hours' : 'days';
    const normalizedValue = clampCalendarLookback(value, normalizedUnit);
    applyCalendarLookbackInputLimits(normalizedUnit);
    if (calendarLookbackUnitSelect) calendarLookbackUnitSelect.value = normalizedUnit;
    if (calendarLookback) calendarLookback.value = String(normalizedValue);
    return { value: normalizedValue, unit: normalizedUnit };
  }

  function saveCalendarLookback(value, unit) {
    try {
      localStorage.setItem(CAL_LOOKBACK_KEY, String(value));
      localStorage.setItem(CAL_UNIT_KEY, unit);
    } catch (_) { /* ignore */ }
  }

  function loadSavedCalendarLookback() {
    try {
      let unit = localStorage.getItem(CAL_UNIT_KEY) || 'days';
      unit = unit === 'hours' ? 'hours' : 'days';
      let raw = localStorage.getItem(CAL_LOOKBACK_KEY);
      if (raw == null) {
        raw = localStorage.getItem(CAL_LEGACY_DAYS_KEY);
        unit = 'days';
      }
      if (raw == null) return;
      setCalendarLookbackControls(parseInt(raw, 10), unit);
    } catch (_) { /* ignore */ }
  }

  function listDaysForCalendarLookback(lookback) {
    if (lookback.unit === 'hours') {
      return Math.max(1, Math.ceil(lookback.value / 24));
    }
    return lookback.value;
  }

  function calendarBackfillWindowLabel(bf) {
    const unit = bf?.unit === 'hours' ? 'hours' : 'days';
    const value =
      bf?.lookback ??
      (unit === 'hours' ? bf?.hours : bf?.days) ??
      calendarReprocessLookback().value;
    return lookbackLabel(value, unit);
  }

  function summaryLookbackUnit() {
    const unit = String(summaryLookbackUnitSelect?.value || 'days').toLowerCase();
    return unit === 'hours' ? 'hours' : 'days';
  }

  function summaryLookbackLimits(unit) {
    if (unit === 'hours') {
      return { min: 1, max: 8760, defaultValue: 24 };
    }
    return { min: 1, max: 365, defaultValue: 5 };
  }

  function clampSummaryLookback(n, unit) {
    const limits = summaryLookbackLimits(unit);
    if (!Number.isFinite(n)) return limits.defaultValue;
    return Math.max(limits.min, Math.min(limits.max, Math.round(n)));
  }

  function convertSummaryLookback(value, fromUnit, toUnit) {
    const from = fromUnit === 'hours' ? 'hours' : 'days';
    const to = toUnit === 'hours' ? 'hours' : 'days';
    if (from === to) return clampSummaryLookback(value, to);
    if (from === 'days' && to === 'hours') {
      return clampSummaryLookback(value * 24, 'hours');
    }
    return clampSummaryLookback(value / 24, 'days');
  }

  function summaryReprocessLookback() {
    const unit = summaryLookbackUnit();
    const n = parseInt(
      summaryLookback?.value || String(summaryLookbackLimits(unit).defaultValue),
      10
    );
    return { value: clampSummaryLookback(n, unit), unit };
  }

  function applySummaryLookbackInputLimits(unit) {
    if (!summaryLookback) return;
    const limits = summaryLookbackLimits(unit);
    summaryLookback.min = String(limits.min);
    summaryLookback.max = String(limits.max);
  }

  function setSummaryLookbackControls(value, unit) {
    const normalizedUnit = unit === 'hours' ? 'hours' : 'days';
    const normalizedValue = clampSummaryLookback(value, normalizedUnit);
    applySummaryLookbackInputLimits(normalizedUnit);
    if (summaryLookbackUnitSelect) summaryLookbackUnitSelect.value = normalizedUnit;
    if (summaryLookback) summaryLookback.value = String(normalizedValue);
    return { value: normalizedValue, unit: normalizedUnit };
  }

  function saveSummaryLookback(value, unit) {
    try {
      localStorage.setItem(SUM_LOOKBACK_KEY, String(value));
      localStorage.setItem(SUM_UNIT_KEY, unit);
    } catch (_) { /* ignore */ }
  }

  function loadSavedSummaryLookback() {
    try {
      let unit = localStorage.getItem(SUM_UNIT_KEY) || 'days';
      unit = unit === 'hours' ? 'hours' : 'days';
      const raw = localStorage.getItem(SUM_LOOKBACK_KEY);
      if (raw == null) return;
      setSummaryLookbackControls(parseInt(raw, 10), unit);
    } catch (_) { /* ignore */ }
  }

  function summaryBackfillWindowLabel(bf) {
    const unit = bf?.unit === 'hours' ? 'hours' : 'days';
    const value =
      bf?.lookback ??
      (unit === 'hours' ? bf?.hours : bf?.days) ??
      summaryReprocessLookback().value;
    return lookbackLabel(value, unit);
  }

  function queueClearResultMessage(cleared, windowText, pendingInWindow, pendingRemaining) {
    const doneText =
      cleared > 0
        ? `Queue emptied — ${cleared} email${cleared === 1 ? '' : 's'} skipped (last ${windowText})`
        : `Queue already empty for the last ${windowText}`;
    const backlogText =
      pendingInWindow === 0
        ? 'backlog clear in that window'
        : `${pendingInWindow} still pending in that window`;
    return `${doneText} · ${backlogText} · ${pendingRemaining} overall`;
  }

  function summaryLookbackPayload() {
    const lookback = summaryReprocessLookback();
    setSummaryLookbackControls(lookback.value, lookback.unit);
    saveSummaryLookback(lookback.value, lookback.unit);
    return {
      lookback,
      windowText: lookbackLabel(lookback.value, lookback.unit),
      body: {
        lookback: lookback.value,
        unit: lookback.unit,
      },
    };
  }

  function setSummaryClearBusy(busy, demoMode) {
    if (btnSummaryClearQueue) btnSummaryClearQueue.disabled = busy || !!demoMode;
    if (summaryLookback) summaryLookback.disabled = busy || !!demoMode;
    if (summaryLookbackUnitSelect) {
      summaryLookbackUnitSelect.disabled = busy || !!demoMode;
    }
  }

  function setSummaryReprocessBusy(busy, demoMode) {
    if (btnSummaryReprocess) btnSummaryReprocess.disabled = busy || !!demoMode;
  }

  function renderSummaryReprocessStatus(pipeline, demoMode) {
    if (!processSummaryStatus) return;
    const bf = pipeline?.summary_backfill || {};
    const queue = pipeline?.queue || {};
    const pending = Number(queue.summary_pending) || 0;
    if (demoMode) {
      processSummaryStatus.textContent = 'Demo mode — summary controls disabled';
      setSummaryClearBusy(true, true);
      setSummaryReprocessBusy(true, true);
      return;
    }
    setSummaryClearBusy(false, false);
    if (bf.running) {
      const windowText = summaryBackfillWindowLabel(bf);
      processSummaryStatus.textContent =
        `Re-processing ${bf.done || 0} / ${bf.total || '…'} (last ${windowText}) · ${pending} in backlog`;
      setSummaryReprocessBusy(true, false);
      return;
    }
    setSummaryReprocessBusy(false, false);
    if ((bf.total || 0) > 0 && (bf.done || 0) >= (bf.total || 0)) {
      processSummaryStatus.textContent =
        `Last run: reprocessed ${bf.total} email${bf.total === 1 ? '' : 's'} (last ${summaryBackfillWindowLabel(bf)}) · ${pending} in backlog`;
      return;
    }
    const lookback = summaryReprocessLookback();
    processSummaryStatus.textContent =
      `Ready — last ${lookbackLabel(lookback.value, lookback.unit)} · ${pending} in backlog`;
  }

  async function clearSummaryQueue() {
    const { windowText, body } = summaryLookbackPayload();
    setSummaryClearBusy(true, false);
    if (processSummaryStatus) {
      processSummaryStatus.textContent = `Emptying summary queue (last ${windowText})…`;
    }
    appendActivityLog(localLogEntry(`Summary queue clear started (last ${windowText})`));
    try {
      const res = await fetch('/api/summary/queue/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Clear queue failed');
      if (Array.isArray(data.logs)) renderActivityLog(data.logs);
      const cleared = Number(data.cleared) || 0;
      const pendingRemaining = Number(data.pending_remaining) ?? Number(data.pipeline?.queue?.summary_pending) ?? 0;
      const pendingInWindow = Number(data.pending_in_window) || 0;
      const doneText = cleared > 0
        ? `Queue emptied — ${cleared} email${cleared === 1 ? '' : 's'} skipped (last ${windowText})`
        : `Queue already empty for the last ${windowText}`;
      appendActivityLog(localLogEntry(doneText));
      await loadProcess();
      if (processSummaryStatus) {
        processSummaryStatus.textContent = queueClearResultMessage(
          cleared,
          windowText,
          pendingInWindow,
          pendingRemaining
        );
      }
    } catch (e) {
      appendActivityLog(localLogEntry(`Summary queue clear error: ${e}`, 'warning'));
      if (processSummaryStatus) {
        processSummaryStatus.textContent = `Clear queue error: ${e}`;
      }
    } finally {
      setSummaryClearBusy(false, false);
    }
  }

  function renderKickstartStatus(demoMode) {
    if (btnKickstartPipelines) btnKickstartPipelines.disabled = !!demoMode;
    if (demoMode && processKickstartStatus) {
      processKickstartStatus.textContent = 'Demo mode — kickstart disabled';
    }
  }

  function renderInvestigateStatus(demoMode) {
    if (demoMode && processPipelineInvestigateStatus && !processPipelineInvestigateStatus.textContent) {
      processPipelineInvestigateStatus.textContent =
        'Demo mode — live pipelines disabled; investigate still reports queue state';
    }
  }

  function renderInvestigateResults(data, options = {}) {
    const resultsEl = options.resultsEl || processPipelineInvestigateResults;
    if (!resultsEl) return;
    const checks = Array.isArray(data?.checks) ? data.checks : [];
    if (!checks.length) {
      resultsEl.hidden = true;
      resultsEl.innerHTML = '';
      return;
    }
    const rows = checks.map((check) => {
      const ok = !!check.ok;
      const severity = String(check.severity || 'error');
      const statusClass = ok
        ? 'process-tts-check-ok'
        : severity === 'warning'
          ? 'process-tts-check-warn'
          : 'process-tts-check-fail';
      const status = ok ? 'OK' : severity === 'warning' ? 'Note' : 'Fail';
      const detail = escapeHtml(String(check.detail || ''));
      const label = escapeHtml(String(check.label || check.id || 'Check'));
      return `<div class="storage-dl-row ${statusClass}"><dt>${label}</dt><dd><span class="process-tts-check-status">${status}</span> ${detail}</dd></div>`;
    });
    const recent =
      Array.isArray(data.recent_pipeline_logs) && data.recent_pipeline_logs.length
        ? data.recent_pipeline_logs
        : Array.isArray(data.recent_tts_logs)
          ? data.recent_tts_logs
          : [];
    const logLabel = options.logLabel || (data.recent_pipeline_logs ? 'Recent pipeline log' : 'Recent TTS log');
    const tips = Array.isArray(data.recommendations) ? data.recommendations : [];
    if (tips.length) {
      const tipLines = tips
        .map((tip) => `<div class="process-tts-recommendation">${escapeHtml(String(tip))}</div>`)
        .join('');
      rows.push(
        `<div class="storage-dl-row process-tts-recommendations-row"><dt>Recommended fix</dt><dd>${tipLines}</dd></div>`
      );
    }
    if (recent.length) {
      const logLines = recent
        .slice()
        .reverse()
        .map((entry) => {
          const ts = escapeHtml(String(entry.ts || ''));
          const msg = escapeHtml(String(entry.message || ''));
          const level = String(entry.level || 'info');
          const levelClass = level === 'warning' || level === 'error' ? ` log-${level}` : '';
          return `<div class="process-tts-log-line${levelClass}"><span class="process-tts-log-ts">${ts}</span> ${msg}</div>`;
        })
        .join('');
      rows.push(
        `<div class="storage-dl-row"><dt>${escapeHtml(logLabel)}</dt><dd class="process-tts-log-block">${logLines}</dd></div>`
      );
    }
    resultsEl.innerHTML = rows.join('');
    resultsEl.hidden = false;
  }

  async function kickstartPipelines() {
    const lookback = calendarReprocessLookback();
    const calendarDays = listDaysForCalendarLookback(lookback);
    if (btnKickstartPipelines) btnKickstartPipelines.disabled = true;
    if (processKickstartStatus) {
      processKickstartStatus.textContent = 'Kickstarting pipelines…';
    }
    appendActivityLog(localLogEntry('Pipeline kickstart started'));
    try {
      const res = await fetch('/api/pipelines/kickstart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ calendar_days: calendarDays }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Kickstart failed');
      if (Array.isArray(data.logs)) renderActivityLog(data.logs);
      if (processKickstartStatus) {
        processKickstartStatus.textContent = data.message || 'Kickstart complete';
      }
      await loadProcess();
      if (
        data.summary_started ||
        data.calendar_started ||
        data.pipeline?.summary_backfill?.running ||
        data.pipeline?.calendar_backfill?.running
      ) {
        schedulePoll();
      }
    } catch (e) {
      appendActivityLog(localLogEntry(`Pipeline kickstart error: ${e}`, 'warning'));
      if (processKickstartStatus) {
        processKickstartStatus.textContent = `Kickstart error: ${e}`;
      }
      if (btnKickstartPipelines) btnKickstartPipelines.disabled = false;
    }
  }

  function setPipelineActionBusy(busy) {
    if (btnInvestigatePipelines) btnInvestigatePipelines.disabled = busy;
  }

  function setTtsActionBusy(busy) {
    if (btnRestartChatterbox) btnRestartChatterbox.disabled = busy;
  }

  async function investigatePipelines() {
    setPipelineActionBusy(true);
    if (processPipelineInvestigateStatus) {
      processPipelineInvestigateStatus.textContent = 'Investigating pipelines…';
      processPipelineInvestigateStatus.classList.remove('calendar-status--warn');
    }
    if (processPipelineInvestigateResults) {
      processPipelineInvestigateResults.hidden = true;
      processPipelineInvestigateResults.innerHTML = '';
    }
    appendActivityLog(localLogEntry('Pipeline investigate started'));
    try {
      const calendarDays = listDaysForCalendarLookback(calendarReprocessLookback());
      const res = await fetch('/api/pipelines/investigate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ list_days: calendarDays }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Pipeline investigate failed');
      if (Array.isArray(data.logs)) renderActivityLog(data.logs);
      if (data.pipeline) renderPipeline(data.pipeline, data.fetch);
      const prefix = data.healthy ? 'Pipelines OK' : 'Pipeline issues';
      if (processPipelineInvestigateStatus) {
        processPipelineInvestigateStatus.textContent = `${prefix}: ${data.message || 'done'}`;
        processPipelineInvestigateStatus.classList.toggle('calendar-status--warn', !data.healthy);
      }
      renderInvestigateResults(data, {
        logLabel: 'Recent pipeline log',
        resultsEl: processPipelineInvestigateResults,
      });
    } catch (e) {
      appendActivityLog(localLogEntry(`Pipeline investigate error: ${e}`, 'warning'));
      if (processPipelineInvestigateStatus) {
        processPipelineInvestigateStatus.textContent = `Pipeline investigate error: ${e}`;
        processPipelineInvestigateStatus.classList.add('calendar-status--warn');
      }
    } finally {
      setPipelineActionBusy(false);
    }
  }

  async function restartChatterbox() {
    setTtsActionBusy(true);
    if (processTtsInvestigateStatus) {
      processTtsInvestigateStatus.textContent = 'Restarting Chatterbox container…';
      processTtsInvestigateStatus.classList.remove('calendar-status--warn');
    }
    if (processTtsInvestigateResults) {
      processTtsInvestigateResults.hidden = true;
      processTtsInvestigateResults.innerHTML = '';
    }
    appendActivityLog(localLogEntry('Chatterbox restart started'));
    try {
      const res = await fetch('/api/pipelines/tts-restart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ verify: true }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Chatterbox restart failed');
      if (Array.isArray(data.logs)) renderActivityLog(data.logs);
      const inv = data.investigate || data;
      const healthy = inv.healthy ?? data.healthy;
      const prefix = healthy ? 'TTS OK' : 'TTS issues';
      if (processTtsInvestigateStatus) {
        processTtsInvestigateStatus.textContent = `${prefix}: ${data.message || 'Restart complete'}`;
        processTtsInvestigateStatus.classList.toggle('calendar-status--warn', !healthy);
      }
      renderInvestigateResults(inv, {
        logLabel: 'Recent TTS log',
        resultsEl: processTtsInvestigateResults,
      });
    } catch (e) {
      appendActivityLog(localLogEntry(`Chatterbox restart error: ${e}`, 'warning'));
      if (processTtsInvestigateStatus) {
        processTtsInvestigateStatus.textContent = `Chatterbox restart error: ${e}`;
        processTtsInvestigateStatus.classList.add('calendar-status--warn');
      }
    } finally {
      setTtsActionBusy(false);
    }
  }

  async function reprocessSummary() {
    const { windowText, body } = summaryLookbackPayload();
    setSummaryReprocessBusy(true, false);
    if (processSummaryStatus) {
      processSummaryStatus.textContent = `Re-processing summary queue (last ${windowText})…`;
    }
    appendActivityLog(localLogEntry(`Summary reprocess started (last ${windowText})`));
    try {
      const res = await fetch('/api/summary/reprocess', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Summary reprocess failed');
      if (Array.isArray(data.logs)) renderActivityLog(data.logs);
      await loadProcess();
      if (data.pipeline?.summary_backfill?.running) {
        schedulePoll();
      }
    } catch (e) {
      setSummaryReprocessBusy(false, false);
      appendActivityLog(localLogEntry(`Summary reprocess error: ${e}`, 'warning'));
      if (processSummaryStatus) {
        processSummaryStatus.textContent = `Re-process error: ${e}`;
      }
    }
  }

  function setCalendarClearBusy(busy, demoMode) {
    if (btnCalendarClearQueue) btnCalendarClearQueue.disabled = busy || !!demoMode;
    if (calendarLookback) calendarLookback.disabled = busy || !!demoMode;
    if (calendarLookbackUnitSelect) calendarLookbackUnitSelect.disabled = busy || !!demoMode;
  }

  function setCalendarReprocessBusy(busy, demoMode) {
    if (btnCalendarReprocess) btnCalendarReprocess.disabled = busy || !!demoMode;
  }

  function renderCalendarReprocessStatus(pipeline, demoMode) {
    if (!processCalendarStatus) return;
    const bf = pipeline?.calendar_backfill || {};
    const queue = pipeline?.queue || {};
    const pending = Number(queue.pending) || 0;
    if (demoMode) {
      processCalendarStatus.textContent = 'Demo mode — calendar controls disabled';
      setCalendarClearBusy(true, true);
      setCalendarReprocessBusy(true, true);
      return;
    }
    setCalendarClearBusy(false, false);
    if (bf.running) {
      const windowText = calendarBackfillWindowLabel(bf);
      processCalendarStatus.textContent =
        `Reprocessing ${bf.done || 0} / ${bf.total || '…'} (last ${windowText}) · ${pending} in queue`;
      setCalendarReprocessBusy(true, false);
      return;
    }
    setCalendarReprocessBusy(false, false);
    if ((bf.total || 0) > 0 && (bf.done || 0) >= (bf.total || 0)) {
      processCalendarStatus.textContent =
        `Last run: reprocessed ${bf.total} email${bf.total === 1 ? '' : 's'} (last ${calendarBackfillWindowLabel(bf)}) · ${pending} in queue`;
      return;
    }
    const lookback = calendarReprocessLookback();
    processCalendarStatus.textContent =
      `Ready — last ${lookbackLabel(lookback.value, lookback.unit)} · ${pending} in queue`;
  }

  function calendarLookbackPayload() {
    const lookback = calendarReprocessLookback();
    setCalendarLookbackControls(lookback.value, lookback.unit);
    saveCalendarLookback(lookback.value, lookback.unit);
    return {
      lookback,
      windowText: lookbackLabel(lookback.value, lookback.unit),
      body: {
        lookback: lookback.value,
        unit: lookback.unit,
        list_days: listDaysForCalendarLookback(lookback),
      },
    };
  }

  async function clearCalendarQueue() {
    const { windowText, body } = calendarLookbackPayload();
    setCalendarClearBusy(true, false);
    if (processCalendarStatus) {
      processCalendarStatus.textContent = `Emptying calendar queue (last ${windowText})…`;
    }
    appendActivityLog(localLogEntry(`Calendar queue clear started (last ${windowText})`));
    try {
      const res = await fetch('/api/calendar/queue/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Clear queue failed');
      if (Array.isArray(data.logs)) renderActivityLog(data.logs);
      const cleared = Number(data.cleared) || 0;
      const pendingRemaining = Number(data.pending_remaining) ?? Number(data.pipeline?.queue?.pending) ?? Number(data.queue?.pending) ?? 0;
      const pendingInWindow = Number(data.pending_in_window) || 0;
      const doneText = cleared > 0
        ? `Queue emptied — ${cleared} email${cleared === 1 ? '' : 's'} skipped (last ${windowText})`
        : `Queue already empty for the last ${windowText}`;
      appendActivityLog(localLogEntry(doneText));
      await loadProcess();
      if (processCalendarStatus) {
        processCalendarStatus.textContent = queueClearResultMessage(
          cleared,
          windowText,
          pendingInWindow,
          pendingRemaining
        );
      }
    } catch (e) {
      appendActivityLog(localLogEntry(`Calendar queue clear error: ${e}`, 'warning'));
      if (processCalendarStatus) {
        processCalendarStatus.textContent = `Clear queue error: ${e}`;
      }
    } finally {
      setCalendarClearBusy(false, false);
    }
  }

  async function reprocessCalendar() {
    const { windowText, body } = calendarLookbackPayload();
    setCalendarReprocessBusy(true, false);
    if (processCalendarStatus) {
      processCalendarStatus.textContent = `Re-processing calendar queue (last ${windowText})…`;
    }
    appendActivityLog(localLogEntry(`Calendar reprocess started (last ${windowText})`));
    try {
      const res = await fetch('/api/calendar/reprocess', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Calendar reprocess failed');
      if (Array.isArray(data.logs)) renderActivityLog(data.logs);
      await loadProcess();
      if (data.backfill?.running) {
        schedulePoll();
      }
    } catch (e) {
      setCalendarReprocessBusy(false, false);
      appendActivityLog(localLogEntry(`Calendar reprocess error: ${e}`, 'warning'));
      if (processCalendarStatus) {
        processCalendarStatus.textContent = `Re-process error: ${e}`;
      }
    }
  }

  function renderPipelineDetails(pipeline, fetchJob) {
    if (!pipelineDetails) return;
    const ollama = pipeline?.ollama || {};
    const queue = pipeline?.queue || {};
    const calBf = pipeline?.calendar_backfill || {};
    const sumBf = pipeline?.summary_backfill || {};
    const pollNote = pipeline?.polling
      ? `every ${Math.round(Number(pipeline.poll_interval) || 0)}s · last ${formatAgo(pipeline.last_poll_at)}`
      : 'not running';

    let sumJob = 'idle';
    if (sumBf.running) {
      sumJob = `summarizing: ${sumBf.done || 0} / ${sumBf.total || '…'} (last ${summaryBackfillWindowLabel(sumBf)})`;
    }

    let calJob = 'idle';
    if (calBf.running) {
      const phase = calBf.phase === 'import' ? 'importing mail' : 'scanning mail';
      calJob = `${phase}: ${calBf.done || 0} / ${calBf.total || '…'} (last ${calendarBackfillWindowLabel(calBf)})`;
      if (calBf.force) calJob += ' (queue cleared)';
    }

    let fetchNote = 'idle';
    if (fetchJob?.running) {
      fetchNote = `${phaseLabel(fetchJob.phase)}: ${fetchJob.done || 0} / ${fetchJob.total || '…'}`;
      if (fetchJob.replace) fetchNote += ' (re-import)';
    } else if ((fetchJob?.total || 0) > 0) {
      fetchNote = `last run: ${fetchJob.total} message${fetchJob.total === 1 ? '' : 's'}`;
    }

    pipelineDetails.innerHTML = [
      dlItem('Mail fetch job', escapeHtml(fetchNote)),
      dlItem('Summary backfill', escapeHtml(sumJob)),
      dlItem('Calendar backfill', escapeHtml(calJob)),
      dlItem('Inbox polling', escapeHtml(pollNote)),
      dlItem(
        'Summary concurrency',
        escapeHtml(String(ollama.summary_concurrency || '—'))
      ),
      dlItem(
        'Calendar concurrency',
        escapeHtml(String(ollama.calendar_concurrency || '—'))
      ),
      dlItem(
        'Spam threshold',
        escapeHtml(String((pipeline?.spam || {}).threshold ?? '—'))
      ),
      dlItem(
        'Calendar GPU',
        escapeHtml(formatGpu(ollama.calendar_gpu))
      ),
      dlItem('Ollama timeout', escapeHtml(`${ollama.timeout || '—'}s`)),
      dlItem(
        'Calendar window',
        escapeHtml(`${queue.list_days || 30} days`)
      ),
    ].join('');
  }

  function renderPipelineUnavailable(message) {
    const text = message || 'Pipeline data unavailable — refresh after the server restarts.';
    if (pipelineStats) {
      pipelineStats.innerHTML = `<p class="storage-empty">${escapeHtml(text)}</p>`;
    }
    if (llmTimings) {
      llmTimings.innerHTML = `<p class="storage-empty">${escapeHtml(text)}</p>`;
    }
    if (pipelineDetails) pipelineDetails.innerHTML = '';
  }

  function renderPipeline(pipeline, fetchJob) {
    if (!pipeline) {
      renderPipelineUnavailable();
      return;
    }
    renderPipelineStats(pipeline);
    renderPipelineSparklines(pipeline);
    renderLlmTimings(pipeline);
    renderPipelineDetails(pipeline, fetchJob);
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
      renderPipeline(data.pipeline, data.fetch);
      renderSummaryReprocessStatus(data.pipeline, data.demo_mode);
      renderCalendarReprocessStatus(data.pipeline, data.demo_mode);
      renderKickstartStatus(data.demo_mode);
      renderInvestigateStatus(data.demo_mode);
      if (Array.isArray(data.logs)) renderActivityLog(data.logs);
      if (
        data.fetch?.running ||
        data.pipeline?.calendar_backfill?.running ||
        data.pipeline?.summary_backfill?.running
      ) {
        setBusy(true, data.demo_mode);
        schedulePoll();
      } else {
        setBusy(false, data.demo_mode);
        clearPoll();
        schedulePipelinePoll();
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
    clearPipelinePoll();
    pollTimer = window.setInterval(() => loadProcess(), 2000);
  }

  function clearPoll() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function schedulePipelinePoll() {
    if (pollTimer) return;
    clearPipelinePoll();
    pipelinePollTimer = window.setInterval(() => loadProcess(), 8000);
  }

  function clearPipelinePoll() {
    if (pipelinePollTimer) {
      window.clearInterval(pipelinePollTimer);
      pipelinePollTimer = null;
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
  summaryLookback?.addEventListener('change', () => {
    const lookback = summaryReprocessLookback();
    setSummaryLookbackControls(lookback.value, lookback.unit);
    saveSummaryLookback(lookback.value, lookback.unit);
    if (processSummaryStatus && !btnSummaryReprocess?.disabled) {
      processSummaryStatus.textContent =
        `Ready — last ${lookbackLabel(lookback.value, lookback.unit)}`;
    }
  });
  summaryLookbackUnitSelect?.addEventListener('change', () => {
    const nextUnit = summaryLookbackUnit();
    const currentValue = parseInt(summaryLookback?.value || '5', 10);
    const converted = convertSummaryLookback(
      currentValue,
      nextUnit === 'hours' ? 'days' : 'hours',
      nextUnit
    );
    const lookback = setSummaryLookbackControls(converted, nextUnit);
    saveSummaryLookback(lookback.value, lookback.unit);
    if (processSummaryStatus && !btnSummaryReprocess?.disabled) {
      processSummaryStatus.textContent =
        `Ready — last ${lookbackLabel(lookback.value, lookback.unit)}`;
    }
  });
  summaryLookback?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !btnSummaryReprocess?.disabled) {
      ev.preventDefault();
      reprocessSummary();
    }
  });
  btnSummaryClearQueue?.addEventListener('click', () => clearSummaryQueue());
  btnSummaryReprocess?.addEventListener('click', () => reprocessSummary());

  calendarLookback?.addEventListener('change', () => {
    const lookback = calendarReprocessLookback();
    setCalendarLookbackControls(lookback.value, lookback.unit);
    saveCalendarLookback(lookback.value, lookback.unit);
    if (processCalendarStatus && !btnCalendarReprocess?.disabled) {
      processCalendarStatus.textContent =
        `Ready — last ${lookbackLabel(lookback.value, lookback.unit)}`;
    }
  });
  calendarLookbackUnitSelect?.addEventListener('change', () => {
    const nextUnit = calendarLookbackUnit();
    const currentValue = parseInt(calendarLookback?.value || '5', 10);
    const converted = convertCalendarLookback(
      currentValue,
      nextUnit === 'hours' ? 'days' : 'hours',
      nextUnit
    );
    const lookback = setCalendarLookbackControls(converted, nextUnit);
    saveCalendarLookback(lookback.value, lookback.unit);
    if (processCalendarStatus && !btnCalendarReprocess?.disabled) {
      processCalendarStatus.textContent =
        `Ready — last ${lookbackLabel(lookback.value, lookback.unit)}`;
    }
  });
  calendarLookback?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !btnCalendarReprocess?.disabled) {
      ev.preventDefault();
      reprocessCalendar();
    }
  });
  btnCalendarClearQueue?.addEventListener('click', () => clearCalendarQueue());
  btnCalendarReprocess?.addEventListener('click', () => reprocessCalendar());
  btnKickstartPipelines?.addEventListener('click', () => kickstartPipelines());
  btnInvestigatePipelines?.addEventListener('click', () => investigatePipelines());
  btnRestartChatterbox?.addEventListener('click', () => restartChatterbox());

  btnClearActivityLog?.addEventListener('click', async () => {
    try {
      await fetch('/api/logs/clear', { method: 'POST' });
      if (processActivityLog) processActivityLog.innerHTML = '';
    } catch (_) { /* ignore */ }
  });

  applyLookbackInputLimits('days');
  applySummaryLookbackInputLimits('days');
  applyCalendarLookbackInputLimits('days');
  loadSavedLookback();
  loadSavedSummaryLookback();
  loadSavedCalendarLookback();
  connectActivityStream();
  schedulePipelinePoll();
  loadProcess();
})();