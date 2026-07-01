(function () {
  const DAYS_KEY = 'smartinbox-calendar-days';
  const LOOKBACK_KEY = 'smartinbox-calendar-lookback';
  const UNIT_KEY = 'smartinbox-calendar-lookback-unit';
  const periodTitle = document.getElementById('period-title');
  const viewHost = document.getElementById('calendar-view-host');
  const eventList = document.getElementById('calendar-event-list');
  const eventsListTitle = document.getElementById('events-list-title');
  const calendarStatus = document.getElementById('calendar-status');
  const calendarInboxes = document.getElementById('calendar-inboxes');
  const calendarDays = document.getElementById('calendar-days');
  const calendarLookback = document.getElementById('calendar-lookback');
  const calendarLookbackUnit = document.getElementById('calendar-lookback-unit');
  const btnRefresh = document.getElementById('btn-refresh-calendar');
  const btnClearQueue = document.getElementById('btn-calendar-clear-queue');
  const btnReprocess = document.getElementById('btn-calendar-reprocess');
  const btnCalPrev = document.getElementById('btn-cal-prev');
  const btnCalNext = document.getElementById('btn-cal-next');
  const btnCalToday = document.getElementById('btn-cal-today');
  const viewTabs = document.querySelectorAll('.calendar-view-tab');
  const calendarActivityLog = document.getElementById('calendar-activity-log');
  const btnClearCalendarActivityLog = document.getElementById('btn-clear-calendar-activity-log');
  const queueSparkline = document.getElementById('calendar-queue-sparkline');
  const queueCount = document.getElementById('calendar-queue-count');
  const queueSparkFill = document.getElementById('calendar-queue-spark-fill');
  const queueSparkPath = document.getElementById('calendar-queue-spark-path');
  const queueNote = document.getElementById('calendar-queue-note');
  let pollTimer = null;
  let queuePollTimer = null;
  let viewMode = 'week';
  let anchorDate = null;
  let selectedEventId = null;
  let selectedEmailId = null;
  let expandedEmailHtml = null;

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

  function buildLogElement(entry) {
    const div = document.createElement('div');
    const lvl = (entry.level || 'info').replace('warning', 'warn');
    const msg = String(entry.message || '');
    const isInboxCheck = msg.startsWith('Inbox check —');
    const isCalendar = msg.startsWith('Calendar');
    div.className = isInboxCheck || isCalendar
      ? 'activity-entry activity-entry--success'
      : 'activity-entry';
    const tsClass = isInboxCheck || isCalendar ? 'success' : lvl;
    div.innerHTML = `<span class="lvl-${tsClass}">[${entry.ts}]</span> ${escapeHtml(msg)}`;
    return div;
  }

  function renderActivityLog(entries) {
    if (!calendarActivityLog) return;
    calendarActivityLog.innerHTML = '';
    [...(entries || [])]
      .sort(compareLogsDesc)
      .slice(0, 120)
      .forEach((entry) => {
        calendarActivityLog.appendChild(buildLogElement(entry));
      });
  }

  function appendActivityLog(entry) {
    if (!calendarActivityLog || !entry) return;
    calendarActivityLog.prepend(buildLogElement(entry));
    while (calendarActivityLog.children.length > 120) {
      calendarActivityLog.removeChild(calendarActivityLog.lastChild);
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
    if (!calendarInboxes) return;
    const connected = (accounts || []).filter((a) => a.connected);
    if (!connected.length) {
      calendarInboxes.textContent =
        'No mail accounts connected — connect Gmail or Proton in Settings to import mail for calendar scanning.';
      return;
    }
    calendarInboxes.textContent = connected
      .map((a) => `${a.label || a.provider}: ${a.email || 'connected'}`)
      .join(' · ');
  }

  function clampDays(n) {
    if (!Number.isFinite(n)) return 5;
    return Math.max(1, Math.min(365, Math.round(n)));
  }

  function lookbackUnit() {
    const unit = String(calendarLookbackUnit?.value || 'days').toLowerCase();
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

  function scanLookback() {
    const unit = lookbackUnit();
    const n = parseInt(
      calendarLookback?.value || String(lookbackLimits(unit).defaultValue),
      10
    );
    return { value: clampLookback(n, unit), unit };
  }

  function lookbackLabel(value, unit) {
    if (unit === 'hours') {
      return `${value} hour${value === 1 ? '' : 's'}`;
    }
    return `${value} day${value === 1 ? '' : 's'}`;
  }

  function applyLookbackInputLimits(unit) {
    if (!calendarLookback) return;
    const limits = lookbackLimits(unit);
    calendarLookback.min = String(limits.min);
    calendarLookback.max = String(limits.max);
  }

  function setLookbackControls(value, unit) {
    const normalizedUnit = unit === 'hours' ? 'hours' : 'days';
    const normalizedValue = clampLookback(value, normalizedUnit);
    applyLookbackInputLimits(normalizedUnit);
    if (calendarLookbackUnit) calendarLookbackUnit.value = normalizedUnit;
    if (calendarLookback) calendarLookback.value = String(normalizedValue);
    return { value: normalizedValue, unit: normalizedUnit };
  }

  function saveLookback(value, unit) {
    try {
      localStorage.setItem(LOOKBACK_KEY, String(value));
      localStorage.setItem(UNIT_KEY, unit);
    } catch (_) { /* ignore */ }
  }

  function listDaysForLookback(lookback) {
    if (lookback.unit === 'hours') {
      return Math.max(1, Math.ceil(lookback.value / 24));
    }
    return lookback.value;
  }

  function scanDays() {
    const n = parseInt(calendarDays?.value || '5', 10);
    return clampDays(n);
  }

  function saveScanDays(days) {
    try {
      localStorage.setItem(DAYS_KEY, String(days));
    } catch (_) { /* ignore */ }
  }

  function backfillWindowLabel(bf) {
    const unit = bf?.unit === 'hours' ? 'hours' : 'days';
    const value =
      bf?.lookback ??
      (unit === 'hours' ? bf?.hours : bf?.days) ??
      scanLookback().value;
    return lookbackLabel(value, unit);
  }

  function loadSavedPreferences() {
    try {
      let unit = localStorage.getItem(UNIT_KEY) || 'days';
      unit = unit === 'hours' ? 'hours' : 'days';
      let rawLookback = localStorage.getItem(LOOKBACK_KEY);
      if (rawLookback == null) {
        rawLookback = localStorage.getItem(DAYS_KEY);
        unit = 'days';
      }
      if (rawLookback != null) {
        setLookbackControls(parseInt(rawLookback, 10), unit);
      } else {
        applyLookbackInputLimits('days');
      }
      const rawDays = localStorage.getItem(DAYS_KEY);
      if (rawDays != null && calendarDays) {
        calendarDays.value = String(clampDays(parseInt(rawDays, 10)));
      }
    } catch (_) { /* ignore */ }
  }

  function parseIsoDate(iso) {
    const [y, m, d] = String(iso || '').split('-').map(Number);
    if (!y || !m || !d) return new Date();
    return new Date(y, m - 1, d);
  }

  function formatIsoDate(dt) {
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, '0');
    const d = String(dt.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }

  function currentAnchor() {
    return anchorDate ? parseIsoDate(anchorDate) : new Date();
  }

  function setAnchorFromIso(iso) {
    anchorDate = iso || null;
  }

  function fmtEventTime(ts) {
    if (!ts) return '';
    const d = new Date(Number(ts) * 1000);
    return d.toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  function fmtChipTime(ts) {
    if (!ts) return '';
    const d = new Date(Number(ts) * 1000);
    return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }

  function fmtEmailTime(ts) {
    if (!ts) return '—';
    return new Date(Number(ts) * 1000).toLocaleString();
  }

  function looksLikeHtml(text) {
    return /<(?:html|body|div|p|table|br|span|td|a|img|style)\b/i.test(text);
  }

  function htmlToEmailText(raw) {
    try {
      const doc = new DOMParser().parseFromString(String(raw), 'text/html');
      doc.querySelectorAll('script, style, head, noscript').forEach((el) => el.remove());
      return (doc.body?.innerText || doc.body?.textContent || '')
        .replace(/\r\n/g, '\n')
        .trim();
    } catch (_) {
      return String(raw || '').trim();
    }
  }

  function plainTextToEmailHtml(text) {
    const normalized = String(text || '').replace(/\r\n/g, '\n').trim();
    if (!normalized) return '<p>(empty message)</p>';
    return normalized
      .split(/\n{2,}/)
      .map((block) => {
        const lines = block
          .split('\n')
          .map((line) => escapeHtml(line))
          .join('<br>');
        return `<p>${lines}</p>`;
      })
      .join('');
  }

  function formatOriginalEmailBody(raw) {
    const text = String(raw || '').trim();
    if (!text) return '<p>(empty message)</p>';
    const plain = looksLikeHtml(text) ? htmlToEmailText(text) : text;
    return plainTextToEmailHtml(plain);
  }

  function buildSourceEmailHtml(row) {
    const from = row.sender || '(unknown)';
    const subject = row.subject || '(no subject)';
    const date = fmtEmailTime(row.received_at || row.created_at);
    const snippetOnly = !row.body_text && !!row.snippet;
    const bodyHtml = formatOriginalEmailBody(row.body_text || row.snippet || '');
    return (
      '<div class="original-email-meta">' +
      `<p><span class="original-label">From</span> ${escapeHtml(from)}</p>` +
      `<p><span class="original-label">Subject</span> ${escapeHtml(subject)}</p>` +
      `<p><span class="original-label">Date</span> ${escapeHtml(date)}</p>` +
      '</div>' +
      (snippetOnly
        ? '<p class="original-email-note">Showing snippet only — full message body is not stored.</p>'
        : '') +
      `<div class="original-email-body">${bodyHtml}</div>`
    );
  }

  function removeInlineExpansions() {
    document.querySelectorAll('.inline-email-expand').forEach((el) => el.remove());
  }

  function findEventAnchor(eventId) {
    if (!eventId) return null;
    const sel = `[data-event-id="${CSS.escape(eventId)}"]`;
    return document.querySelector(`.calendar-day-event${sel}, .event-item${sel}`);
  }

  function createInlineExpandShell(title) {
    const expand = document.createElement('div');
    expand.className = 'inline-email-expand';
    expand.innerHTML =
      `<div class="inline-email-expand-head">
        <span class="inline-email-expand-title">${escapeHtml(title)}</span>
        <button type="button" class="btn btn-secondary btn-small inline-email-expand-close">Close</button>
      </div>
      <div class="inline-email-expand-body original-email-view"></div>`;
    expand.querySelector('.inline-email-expand-close')?.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      closeSourceEmail();
    });
    return expand;
  }

  async function loadSourceEmailInto(bodyEl, emailId) {
    if (!bodyEl || !emailId) return;
    bodyEl.innerHTML = '<p class="calendar-empty">Loading original email…</p>';
    try {
      const res = await fetch(`/api/emails/${encodeURIComponent(emailId)}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Email not found');
      expandedEmailHtml = buildSourceEmailHtml(data.email);
      bodyEl.innerHTML = expandedEmailHtml;
    } catch (e) {
      expandedEmailHtml = null;
      bodyEl.innerHTML = `<p class="calendar-empty">Could not load email: ${escapeHtml(String(e))}</p>`;
    }
  }

  function restoreInlineExpansion(eventId, emailId) {
    if (!eventId || !emailId) return;
    const anchor = findEventAnchor(eventId);
    if (!anchor) return;
    removeInlineExpansions();
    const expand = createInlineExpandShell('Source email');
    expand.dataset.forEventId = eventId;
    expand.dataset.forEmailId = emailId;
    anchor.classList.add('is-expanded');
    anchor.insertAdjacentElement('afterend', expand);
    const bodyEl = expand.querySelector('.inline-email-expand-body');
    if (expandedEmailHtml && selectedEmailId === emailId) {
      bodyEl.innerHTML = expandedEmailHtml;
    } else {
      loadSourceEmailInto(bodyEl, emailId);
    }
  }

  function markSelectedEvent(eventId) {
    selectedEventId = eventId || null;
    document.querySelectorAll('.calendar-day-event.is-selected, .event-item.is-selected').forEach((el) => {
      el.classList.remove('is-selected');
    });
    if (!eventId) return;
    document.querySelectorAll(`[data-event-id="${CSS.escape(eventId)}"]`).forEach((el) => {
      el.classList.add('is-selected');
    });
  }

  async function showSourceEmail(emailId, eventId) {
    if (!emailId || !eventId) return;
    if (selectedEventId === eventId) {
      closeSourceEmail();
      return;
    }
    selectedEventId = eventId;
    selectedEmailId = emailId;
    markSelectedEvent(eventId);
    removeInlineExpansions();
    const anchor = findEventAnchor(eventId);
    if (!anchor) return;
    const expand = createInlineExpandShell('Source email');
    expand.dataset.forEventId = eventId;
    expand.dataset.forEmailId = emailId;
    anchor.classList.add('is-expanded');
    anchor.insertAdjacentElement('afterend', expand);
    const bodyEl = expand.querySelector('.inline-email-expand-body');
    expand.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    await loadSourceEmailInto(bodyEl, emailId);
  }

  function closeSourceEmail() {
    selectedEventId = null;
    selectedEmailId = null;
    expandedEmailHtml = null;
    removeInlineExpansions();
    markSelectedEvent(null);
    document.querySelectorAll('.calendar-day-event.is-expanded, .event-item.is-expanded').forEach((el) => {
      el.classList.remove('is-expanded');
    });
  }

  function bindEventSourceClicks(root) {
    if (!root) return;
    root.querySelectorAll('.calendar-day-event[data-email-id]').forEach((el) => {
      el.addEventListener('click', (ev) => {
        if (
          ev.target.closest('.event-votes') ||
          ev.target.closest('.vote-btn') ||
          ev.target.closest('.event-remove-btn')
        ) return;
        showSourceEmail(el.dataset.emailId, el.dataset.eventId);
      });
      el.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        if (ev.target.closest('.vote-btn') || ev.target.closest('.event-remove-btn')) return;
        ev.preventDefault();
        showSourceEmail(el.dataset.emailId, el.dataset.eventId);
      });
    });
    root.querySelectorAll('.event-item[data-email-id]').forEach((el) => {
      const body = el.querySelector('.event-item-body');
      if (!body) return;
      body.addEventListener('click', (ev) => {
        if (ev.target.closest('.vote-btn')) return;
        showSourceEmail(el.dataset.emailId, el.dataset.eventId);
      });
    });
  }

  function buildChip(ev, compact) {
    const title = escapeHtml(ev.title || 'Event');
    const time = escapeHtml(fmtChipTime(ev.event_start));
    const tip = escapeHtml(ev.source_text || '');
    if (compact) {
      return `<div class="calendar-chip calendar-chip--compact" title="${tip}"><span class="calendar-chip-title">${title}</span></div>`;
    }
    return `<div class="calendar-chip" title="${tip}">
      <span class="calendar-chip-time">${time}</span>
      <span class="calendar-chip-title">${title}</span>
    </div>`;
  }

  function renderDayChips(events, compact, maxVisible) {
    if (!events || !events.length) {
      return '<span class="calendar-day-empty">—</span>';
    }
    const limit = maxVisible || events.length;
    const visible = events.slice(0, limit);
    const extra = events.length - visible.length;
    let html = visible.map((ev) => buildChip(ev, compact)).join('');
    if (extra > 0) {
      html += `<span class="calendar-more">+${extra} more</span>`;
    }
    return html;
  }

  function openDayView(date) {
    if (!date) return;
    setAnchorFromIso(date);
    setViewMode('day');
    loadCalendar();
  }

  function bindDayClicks(root) {
    if (!root) return;
    root.querySelectorAll('[data-goto-date]').forEach((el) => {
      el.addEventListener('click', () => openDayView(el.dataset.gotoDate));
      el.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          openDayView(el.dataset.gotoDate);
        }
      });
    });
  }

  function renderWeek(period) {
    const days = period?.days || [];
    if (!days.length) {
      return '<p class="calendar-empty">No week data.</p>';
    }
    return `<div class="calendar-week-grid">${days
      .map((day) => {
        const todayClass = day.is_today ? ' is-today' : '';
        const chips = renderDayChips(day.events, false, 8);
        return `<div class="calendar-day${todayClass}" data-goto-date="${escapeHtml(day.date)}" role="button" tabindex="0" title="Open day view">
          <div class="calendar-day-head">${escapeHtml(day.label || day.weekday || '')}</div>
          <div class="calendar-day-body">${chips}</div>
        </div>`;
      })
      .join('')}</div>`;
  }

  function renderMonth(period) {
    const labels = period?.weekday_labels || ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const weeks = period?.weeks || [];
    if (!weeks.length) {
      return '<p class="calendar-empty">No month data.</p>';
    }
    const header = labels
      .map((label) => `<div class="calendar-month-weekday">${escapeHtml(label)}</div>`)
      .join('');
    const body = weeks
      .map((week) => {
        const days = (week.days || [])
          .map((day) => {
            const classes = ['calendar-month-day'];
            if (!day.in_month) classes.push('outside-month');
            if (day.is_today) classes.push('is-today');
            const chips = renderDayChips(day.events, true, 3);
            return `<div class="${classes.join(' ')}" data-goto-date="${escapeHtml(day.date)}" role="button" tabindex="0" title="Open day view">
              <div class="calendar-month-day-num">${day.day_num}</div>
              <div class="calendar-month-day-events">${chips}</div>
            </div>`;
          })
          .join('');
        return `<div class="calendar-month-week">${days}</div>`;
      })
      .join('');
    return `<div class="calendar-month-grid"><div class="calendar-month-head">${header}</div><div class="calendar-month-body">${body}</div></div>`;
  }

  function buildVoteButtons(ev) {
    const lastVote = ev.last_vote || '';
    const upActive = lastVote === 'up' ? ' vote-active' : '';
    const downActive = lastVote === 'down' ? ' vote-active' : '';
    return `<div class="event-votes" role="group" aria-label="Rate event">
      <button type="button" class="vote-btn vote-up${upActive}" data-vote="up" title="Keep on calendar" aria-label="Upvote event">▲</button>
      <button type="button" class="vote-btn vote-down${downActive}" data-vote="down" title="Hide from calendar views" aria-label="Downvote event">▼</button>
    </div>`;
  }

  function buildDayEventActions(ev) {
    return `<div class="calendar-day-event-actions">
      ${buildVoteButtons(ev)}
      <button type="button" class="btn btn-secondary btn-small event-remove-btn" title="Remove this event from the calendar">Remove</button>
    </div>`;
  }

  function bindVoteButtons(root) {
    if (!root) return;
    root.querySelectorAll('[data-id]').forEach((el) => {
      el.querySelectorAll('.vote-btn').forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          voteEvent(el.dataset.id, btn.dataset.vote);
        });
      });
    });
  }

  function bindRemoveButtons(root) {
    if (!root) return;
    root.querySelectorAll('[data-id]').forEach((el) => {
      const btn = el.querySelector('.event-remove-btn');
      if (!btn) return;
      btn.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        removeEvent(el.dataset.id);
      });
    });
  }

  function renderDay(period) {
    const events = period?.events || [];
    if (!events.length) {
      return '<p class="calendar-empty">No events on this day.</p>';
    }
    return `<div class="calendar-day-view">${events
      .map((ev) => {
        const meta = [ev.sender, ev.subject].filter(Boolean).map(escapeHtml).join(' · ');
        const hasEmail = !!ev.email_id;
        const eventClasses = ['calendar-day-event'];
        if (hasEmail) eventClasses.push('calendar-day-event--clickable');
        const eventAttrs = hasEmail
          ? ` data-email-id="${escapeHtml(ev.email_id)}" role="button" tabindex="0" title="View source email"`
          : '';
        return `<div class="${eventClasses.join(' ')}" data-event-id="${escapeHtml(ev.id)}" data-id="${escapeHtml(ev.id)}"${eventAttrs}>
          ${buildDayEventActions(ev)}
          <div class="calendar-day-event-time">${escapeHtml(fmtChipTime(ev.event_start))}</div>
          <div class="calendar-day-event-body">
            <div class="calendar-day-event-title">${escapeHtml(ev.title || 'Event')}</div>
            ${meta ? `<div class="calendar-day-event-meta">${meta}</div>` : ''}
            ${ev.source_text ? `<div class="calendar-day-event-source">"${escapeHtml(ev.source_text)}"</div>` : ''}
          </div>
        </div>`;
      })
      .join('')}</div>`;
  }

  function renderCalendarView(data) {
    if (!viewHost) return;
    const mode = data.view_mode || viewMode;
    const period = data.period || data.week || {};
    if (periodTitle && period.label) {
      periodTitle.textContent = period.label;
    }
    let html = '';
    if (mode === 'month') html = renderMonth(period);
    else if (mode === 'day') html = renderDay(period);
    else html = renderWeek(period);
    viewHost.innerHTML = html;
    bindDayClicks(viewHost);
    if (mode === 'day') {
      bindVoteButtons(viewHost);
      bindRemoveButtons(viewHost);
      bindEventSourceClicks(viewHost);
    }
    if (selectedEventId && selectedEmailId) {
      markSelectedEvent(selectedEventId);
      restoreInlineExpansion(selectedEventId, selectedEmailId);
    }
  }

  function setViewMode(mode) {
    if (!['month', 'week', 'day'].includes(mode)) return;
    viewMode = mode;
    viewTabs.forEach((tab) => {
      const active = tab.dataset.view === mode;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }

  function navigatePrev() {
    const dt = currentAnchor();
    if (viewMode === 'month') {
      dt.setMonth(dt.getMonth() - 1);
    } else if (viewMode === 'day') {
      dt.setDate(dt.getDate() - 1);
    } else {
      dt.setDate(dt.getDate() - 7);
    }
    setAnchorFromIso(formatIsoDate(dt));
    loadCalendar();
  }

  function navigateNext() {
    const dt = currentAnchor();
    if (viewMode === 'month') {
      dt.setMonth(dt.getMonth() + 1);
    } else if (viewMode === 'day') {
      dt.setDate(dt.getDate() + 1);
    } else {
      dt.setDate(dt.getDate() + 7);
    }
    setAnchorFromIso(formatIsoDate(dt));
    loadCalendar();
  }

  function goToday() {
    anchorDate = null;
    loadCalendar();
  }

  function renderEventList(events) {
    if (!eventList) return;
    if (!events || !events.length) {
      eventList.innerHTML =
        '<p class="calendar-empty">No events found yet. Use <strong>Re-process</strong> on stored mail, or <a href="/pipelines">Pipelines</a> to import more.</p>';
      return;
    }
    eventList.innerHTML = events
      .map((ev) => {
        const hidden = ev.hidden ? ' hidden-event' : '';
        const hiddenBadge = ev.hidden
          ? '<span class="calendar-hidden-badge">hidden from calendar</span>'
          : '';
        const meta = [
          fmtEventTime(ev.event_start),
          ev.sender ? escapeHtml(ev.sender) : '',
          ev.subject ? escapeHtml(ev.subject) : '',
        ]
          .filter(Boolean)
          .join(' · ');
        const emailAttr = ev.email_id ? ` data-email-id="${escapeHtml(ev.email_id)}"` : '';
        const bodyClass = ev.email_id ? ' event-item-body--clickable' : '';
        const bodyTitle = ev.email_id ? ' title="View source email"' : '';
        return `<div class="event-item${hidden}" data-event-id="${escapeHtml(ev.id)}" data-id="${escapeHtml(ev.id)}"${emailAttr}>
          ${buildVoteButtons(ev)}
          <div class="event-item-body${bodyClass}"${bodyTitle}>
            <div class="event-title">${escapeHtml(ev.title || 'Event')}${hiddenBadge}</div>
            <div class="event-meta">${meta}</div>
            ${ev.source_text ? `<div class="event-source">"${escapeHtml(ev.source_text)}"</div>` : ''}
          </div>
        </div>`;
      })
      .join('');

    bindVoteButtons(eventList);
    bindEventSourceClicks(eventList);
    if (selectedEventId && selectedEmailId) {
      markSelectedEvent(selectedEventId);
      restoreInlineExpansion(selectedEventId, selectedEmailId);
    }
  }

  function renderQueueSparkline(queue) {
    if (!queueSparkline || !queue) return;
    const pending = Number(queue.pending) || 0;
    if (queueCount) queueCount.textContent = String(pending);

    const history = Array.isArray(queue.history) ? queue.history : [];
    const values = history.map((point) => Number(point.pending) || 0);
    if (!values.length) values.push(pending);

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
    if (queueSparkPath) queueSparkPath.setAttribute('d', linePath);
    if (queueSparkFill) queueSparkFill.setAttribute('d', fillPath);

    queueSparkline.classList.toggle('is-high', pending >= 25);
    queueSparkline.classList.toggle('is-clear', pending === 0);

    if (!queueNote) return;
    const notes = [];
    const days = queue.list_days || scanDays();
    notes.push(`last ${days} day${days === 1 ? '' : 's'} of stored mail`);
    if (queue.calendar_scan_running && queue.scan_total > 0) {
      notes.push(`scanning ${queue.scan_done || 0}/${queue.scan_total}`);
    } else if (queue.blocked_by_summary && queue.summary_pending > 0) {
      notes.push(`LLM deferred — ${queue.summary_pending} summaries pending`);
    } else if (pending === 0 && (queue.scanned_no_events || 0) > 0) {
      notes.push(`idle — ${queue.scanned_no_events} scanned with no dates`);
    } else if (pending === 0 && !queue.calendar_scan_running) {
      notes.push('idle — queue clear');
    }
    queueNote.textContent = notes.join(' · ');
  }

  async function loadQueueStats() {
    const days = scanDays();
    try {
      const res = await fetch(`/api/calendar/queue?list_days=${encodeURIComponent(days)}`);
      const data = await res.json();
      if (!data.ok) return;
      renderQueueSparkline(data.queue);
    } catch (_) { /* ignore */ }
  }

  function renderStatus(data) {
    if (!calendarStatus) return;
    const parts = [];
    if (data.demo_mode) {
      parts.push('Demo mode — sample calendar events (Check for Events disabled)');
    }
    const bf = data.backfill || {};
    const windowLabel = backfillWindowLabel(bf);
    const providerNote = formatProviderCounts(bf.by_provider);
    if (bf.running) {
      if (bf.phase === 'import') {
        parts.push(`Importing mail from all inboxes (last ${windowLabel})…`);
      } else {
        const verb = bf.force ? 'Reprocessing' : 'Processing';
        const parallel = data.extract_concurrency || 1;
        let line = `${verb} stored mail ${bf.done || 0} / ${bf.total || 0} (last ${windowLabel}, ${parallel} parallel)`;
        if (providerNote) line += ` — ${providerNote}`;
        parts.push(`${line}…`);
      }
    } else if ((bf.total || 0) > 0 && (bf.done || 0) >= (bf.total || 0)) {
      const verb = bf.force ? 'Reprocessed' : 'Processed';
      const parallel = data.extract_concurrency || 1;
      let line = `${verb} ${bf.total} stored emails (last ${windowLabel}, ${parallel} parallel)`;
      if (providerNote) line += ` — ${providerNote}`;
      parts.push(line);
    }
    calendarStatus.textContent = parts.join(' · ');
    if (eventsListTitle) {
      const listDays = data.list_days || scanDays();
      eventsListTitle.textContent = `Key events (last ${listDays} days)`;
    }
  }

  function setClearBusy(busy, demoMode) {
    if (btnClearQueue) btnClearQueue.disabled = busy || !!demoMode;
    if (calendarLookback) calendarLookback.disabled = busy || !!demoMode;
    if (calendarLookbackUnit) calendarLookbackUnit.disabled = busy || !!demoMode;
  }

  function setReprocessBusy(busy, demoMode) {
    if (btnReprocess) btnReprocess.disabled = busy || !!demoMode;
  }

  function setScanBusy(busy, demoMode) {
    setClearBusy(busy, demoMode);
    setReprocessBusy(busy, demoMode);
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

  async function loadCalendar() {
    const days = scanDays();
    const params = new URLSearchParams({
      list_days: String(days),
      view: viewMode,
    });
    if (anchorDate) params.set('anchor', anchorDate);
    try {
      const res = await fetch(`/api/calendar?${params.toString()}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Failed to load calendar');
      if (data.view_mode) setViewMode(data.view_mode);
      if (data.anchor) setAnchorFromIso(data.anchor);
      renderCalendarView(data);
      renderConnectedInboxes(data.mail_accounts);
      renderEventList(data.events);
      renderStatus(data);
      renderQueueSparkline(data.queue);
      if (data.demo_mode) {
        setClearBusy(true, true);
        setReprocessBusy(true, true);
      } else {
        setClearBusy(false, false);
        if (data.backfill?.running) {
          setReprocessBusy(true, false);
          schedulePoll();
        } else {
          setReprocessBusy(false, false);
          clearPoll();
        }
      }
    } catch (e) {
      setScanBusy(false, false);
      if (calendarStatus) calendarStatus.textContent = `Error: ${e}`;
      if (viewHost) {
        viewHost.innerHTML = '<p class="calendar-empty">Could not load calendar.</p>';
      }
      if (eventList) {
        eventList.innerHTML = '<p class="calendar-empty">Could not load events.</p>';
      }
    }
  }

  async function voteEvent(eventId, vote) {
    if (!eventId || !vote) return;
    try {
      const res = await fetch(`/api/calendar/events/${encodeURIComponent(eventId)}/vote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vote }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Vote failed');
      await loadCalendar();
    } catch (e) {
      if (calendarStatus) calendarStatus.textContent = `Vote error: ${e}`;
    }
  }

  async function removeEvent(eventId) {
    if (!eventId) return;
    try {
      const res = await fetch(`/api/calendar/events/${encodeURIComponent(eventId)}/remove`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Remove failed');
      if (selectedEventId === eventId) closeSourceEmail();
      await loadCalendar();
    } catch (e) {
      if (calendarStatus) calendarStatus.textContent = `Remove error: ${e}`;
    }
  }

  function calendarLookbackPayload() {
    const lookback = scanLookback();
    setLookbackControls(lookback.value, lookback.unit);
    saveLookback(lookback.value, lookback.unit);
    return {
      windowText: lookbackLabel(lookback.value, lookback.unit),
      body: {
        lookback: lookback.value,
        unit: lookback.unit,
        list_days: listDaysForLookback(lookback),
      },
    };
  }

  async function clearCalendarQueue() {
    const { windowText, body } = calendarLookbackPayload();
    setClearBusy(true, false);
    if (calendarStatus) {
      calendarStatus.textContent = `Emptying calendar queue (last ${windowText})…`;
    }
    try {
      const res = await fetch('/api/calendar/queue/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Clear queue failed');
      const cleared = Number(data.cleared) || 0;
      const pendingRemaining = Number(data.pending_remaining) ?? Number(data.queue?.pending) ?? 0;
      const pendingInWindow = Number(data.pending_in_window) || 0;
      await loadCalendar();
      if (calendarStatus) {
        calendarStatus.textContent = queueClearResultMessage(
          cleared,
          windowText,
          pendingInWindow,
          pendingRemaining
        );
      }
    } catch (e) {
      if (calendarStatus) calendarStatus.textContent = `Clear queue error: ${e}`;
    } finally {
      setClearBusy(false, false);
    }
  }

  async function reprocessCalendar() {
    const { windowText, body } = calendarLookbackPayload();
    setReprocessBusy(true, false);
    if (calendarStatus) {
      calendarStatus.textContent = `Re-processing calendar queue (last ${windowText})…`;
    }
    try {
      const res = await fetch('/api/calendar/reprocess', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Reprocess failed');
      await loadCalendar();
    } catch (e) {
      if (calendarStatus) calendarStatus.textContent = `Re-process error: ${e}`;
      setReprocessBusy(false, false);
    }
  }

  function commitDaysInput() {
    if (!calendarDays) return;
    const days = scanDays();
    calendarDays.value = String(days);
    saveScanDays(days);
    loadCalendar();
  }

  function schedulePoll() {
    clearPoll();
    pollTimer = window.setInterval(() => loadCalendar(), 4000);
  }

  function clearPoll() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function scheduleQueuePoll() {
    if (queuePollTimer) return;
    queuePollTimer = window.setInterval(() => loadQueueStats(), 8000);
  }

  function clearQueuePoll() {
    if (queuePollTimer) {
      window.clearInterval(queuePollTimer);
      queuePollTimer = null;
    }
  }

  viewTabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const mode = tab.dataset.view;
      if (!mode || mode === viewMode) return;
      setViewMode(mode);
      loadCalendar();
    });
  });

  calendarDays?.addEventListener('change', () => commitDaysInput());
  calendarDays?.addEventListener('blur', () => {
    if (!calendarDays) return;
    calendarDays.value = String(scanDays());
  });
  calendarDays?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      commitDaysInput();
    }
  });

  btnCalPrev?.addEventListener('click', () => navigatePrev());
  btnCalNext?.addEventListener('click', () => navigateNext());
  btnCalToday?.addEventListener('click', () => goToday());
  btnRefresh?.addEventListener('click', () => loadCalendar());
  calendarLookback?.addEventListener('change', () => {
    const lookback = scanLookback();
    setLookbackControls(lookback.value, lookback.unit);
    saveLookback(lookback.value, lookback.unit);
  });
  calendarLookbackUnit?.addEventListener('change', () => {
    const nextUnit = lookbackUnit();
    const currentValue = parseInt(calendarLookback?.value || '5', 10);
    const converted = convertLookback(
      currentValue,
      nextUnit === 'hours' ? 'days' : 'hours',
      nextUnit
    );
    const lookback = setLookbackControls(converted, nextUnit);
    saveLookback(lookback.value, lookback.unit);
  });
  calendarLookback?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !btnReprocess?.disabled) {
      ev.preventDefault();
      reprocessCalendar();
    }
  });
  btnClearQueue?.addEventListener('click', () => clearCalendarQueue());
  btnReprocess?.addEventListener('click', () => reprocessCalendar());
  btnClearCalendarActivityLog?.addEventListener('click', async () => {
    try {
      await fetch('/api/logs/clear', { method: 'POST' });
      if (calendarActivityLog) calendarActivityLog.innerHTML = '';
    } catch (_) { /* ignore */ }
  });

  connectActivityStream();
  loadSavedPreferences();
  setViewMode('week');
  scheduleQueuePoll();
  loadCalendar();
  window.addEventListener('beforeunload', () => {
    clearPoll();
    clearQueuePoll();
  });
})();