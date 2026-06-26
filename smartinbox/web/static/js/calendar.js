(function () {
  const DAYS_KEY = 'smartinbox-calendar-days';
  const VIEW_KEY = 'smartinbox-calendar-view';

  const periodTitle = document.getElementById('period-title');
  const viewHost = document.getElementById('calendar-view-host');
  const eventList = document.getElementById('calendar-event-list');
  const eventsListTitle = document.getElementById('events-list-title');
  const calendarStatus = document.getElementById('calendar-status');
  const calendarInboxes = document.getElementById('calendar-inboxes');
  const calendarDays = document.getElementById('calendar-days');
  const btnRefresh = document.getElementById('btn-refresh-calendar');
  const btnProcess = document.getElementById('btn-process-calendar');
  const btnCalPrev = document.getElementById('btn-cal-prev');
  const btnCalNext = document.getElementById('btn-cal-next');
  const btnCalToday = document.getElementById('btn-cal-today');
  const viewTabs = document.querySelectorAll('.calendar-view-tab');
  const calendarActivityLog = document.getElementById('calendar-activity-log');
  const btnClearCalendarActivityLog = document.getElementById('btn-clear-calendar-activity-log');
  let pollTimer = null;
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

  function scanDays() {
    const n = parseInt(calendarDays?.value || '5', 10);
    return clampDays(n);
  }

  function saveScanDays(days) {
    try {
      localStorage.setItem(DAYS_KEY, String(days));
    } catch (_) { /* ignore */ }
  }

  function saveViewMode(mode) {
    try {
      localStorage.setItem(VIEW_KEY, mode);
    } catch (_) { /* ignore */ }
  }

  function loadSavedPreferences() {
    try {
      const rawDays = localStorage.getItem(DAYS_KEY);
      if (rawDays != null && calendarDays) {
        calendarDays.value = String(clampDays(parseInt(rawDays, 10)));
      }
      const rawView = localStorage.getItem(VIEW_KEY);
      if (rawView && ['month', 'week', 'day'].includes(rawView)) {
        viewMode = rawView;
        setViewMode(viewMode);
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
        if (ev.target.closest('.event-votes') || ev.target.closest('.vote-btn')) return;
        showSourceEmail(el.dataset.emailId, el.dataset.eventId);
      });
      el.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        if (ev.target.closest('.vote-btn')) return;
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
    saveViewMode('day');
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
          ${buildVoteButtons(ev)}
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
        '<p class="calendar-empty">No events found yet. Use <strong>Check for Events</strong> on stored mail, or <a href="/process">Process</a> to import more.</p>';
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

  function renderStatus(data) {
    if (!calendarStatus) return;
    const parts = [];
    if (data.demo_mode) {
      parts.push('Demo mode — sample calendar events (Check for Events disabled)');
    }
    const bf = data.backfill || {};
    const days = bf.days || data.list_days || scanDays();
    const providerNote = formatProviderCounts(bf.by_provider);
    if (bf.running) {
      if (bf.phase === 'import') {
        parts.push(`Importing mail from all inboxes (last ${days} days)…`);
      } else {
        const verb = bf.force ? 'Checking' : 'Processing';
        const parallel = data.extract_concurrency || 1;
        let line = `${verb} stored mail ${bf.done || 0} / ${bf.total || 0} (last ${days} days, ${parallel} parallel)`;
        if (providerNote) line += ` — ${providerNote}`;
        parts.push(`${line}…`);
      }
    } else if ((bf.total || 0) > 0 && (bf.done || 0) >= (bf.total || 0)) {
      const verb = bf.force ? 'Checked' : 'Processed';
      const parallel = data.extract_concurrency || 1;
      let line = `${verb} ${bf.total} stored emails (last ${days} days, ${parallel} parallel)`;
      if (providerNote) line += ` — ${providerNote}`;
      parts.push(line);
    }
    calendarStatus.textContent = parts.join(' · ');
    if (eventsListTitle) {
      const listDays = data.list_days || scanDays();
      eventsListTitle.textContent = `Key events (last ${listDays} days)`;
    }
  }

  function setScanBusy(busy, demoMode) {
    if (btnProcess) btnProcess.disabled = busy || !!demoMode;
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
      if (data.backfill?.running) {
        setScanBusy(true, data.demo_mode);
        schedulePoll();
      } else {
        setScanBusy(false, data.demo_mode);
        clearPoll();
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

  async function checkForEvents() {
    const days = scanDays();
    saveScanDays(days);
    setScanBusy(true, false);
    if (calendarStatus) {
      calendarStatus.textContent = `Checking stored mail for events (last ${days} days)…`;
    }
    try {
      const res = await fetch('/api/calendar/backfill', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days, force: true, import_from_source: false, list_days: days }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Check failed');
      await loadCalendar();
    } catch (e) {
      if (calendarStatus) calendarStatus.textContent = `Check error: ${e}`;
      setScanBusy(false, false);
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

  viewTabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const mode = tab.dataset.view;
      if (!mode || mode === viewMode) return;
      setViewMode(mode);
      saveViewMode(mode);
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
  btnProcess?.addEventListener('click', () => checkForEvents());
  btnClearCalendarActivityLog?.addEventListener('click', async () => {
    try {
      await fetch('/api/logs/clear', { method: 'POST' });
      if (calendarActivityLog) calendarActivityLog.innerHTML = '';
    } catch (_) { /* ignore */ }
  });

  connectActivityStream();
  loadSavedPreferences();
  loadCalendar();
})();