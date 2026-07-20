(function () {
  const inboxList = document.getElementById('inbox-list');
  const inboxCount = document.getElementById('inbox-count');
  const summaryViewport = document.getElementById('summary-viewport');
  const summaryBody = document.getElementById('summary-body');
  const summaryTheme = document.getElementById('summary-theme');
  const activityLog = document.getElementById('activity-log');
  const activityLogPanel = document.getElementById('activity-log-panel');
  const connStatus = document.getElementById('conn-status');
  const btnPoll = document.getElementById('btn-poll');
  const btnEmptyInbox = document.getElementById('btn-empty-inbox');
  const btnClearActivityLog = document.getElementById('btn-clear-activity-log');
  const btnHideActivityLog = document.getElementById('btn-hide-activity-log');
  const btnShowActivityLog = document.getElementById('btn-show-activity-log');
  const btnResummarize = document.getElementById('btn-resummarize');
  const summaryViewOptions = document.querySelectorAll('.summary-view-option');
  const volumeSlider = document.getElementById('alert-volume');

  const THEME_KEY = 'smartinbox.summaryTheme';
  const INBOX_HIDDEN_KEY = 'smartinbox-inbox-hidden';
  const VOLUME_KEY = 'smartinbox-alert-volume';
  const ACTIVITY_LOG_HIDDEN_KEY = 'smartinbox-activity-log-hidden';
  const THEME_OPTIONS = [
    { value: 'mainframe', label: '1970s mainframe' },
    { value: 'dotmatrix', label: '80s dot matrix printer' },
    { value: 'amber', label: 'Amber terminal' },
    { value: 'ansi', label: 'ANSI color terminal' },
    { value: 'blueprint', label: 'Blueprint' },
    { value: 'c64', label: 'Commodore 64' },
    { value: 'dune1984', label: 'Dune 1984' },
    { value: 'computer50s', label: 'Early 1950s computer' },
    { value: 'empire', label: 'Galactic Empire' },
    { value: 'phosphor', label: 'Green phosphor CRT' },
    { value: 'kawaiimail', label: 'Kawaii Mail' },
    { value: 'lsmail', label: 'Leisure Suit Mailman' },
    { value: 'teleprinter', label: 'Line printer' },
    { value: 'logansrun', label: "Logan's Run" },
    { value: 'macintosh', label: 'Macintosh' },
    { value: 'mailtrek', label: 'Mail Trek (LCARS)' },
    { value: 'mailcraft', label: 'MailCraft' },
    { value: 'modern', label: 'Modern display' },
    { value: 'nasa70s', label: 'NASA Mission Control' },
    { value: 'newsprint', label: 'Newsprint' },
    { value: 'pacmail', label: 'PacMail' },
    { value: 'pdp11', label: 'PDP-11 terminal' },
    { value: 'reddwarf', label: 'Red Dwarf' },
    { value: 'arcade', label: 'Retro arcade CRT' },
    { value: 'solarized', label: 'Solarized' },
    { value: 'tripleplanets', label: 'Triple Planets' },
    { value: 'typewriter', label: 'Typewriter' },
    { value: 'weylandyutani', label: 'Weyland-Yutani Corp' },
  ].sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }));
  const THEMES = THEME_OPTIONS.map((t) => t.value);

  let emails = [];
  let demoMode = false;
  let hiddenInboxIds = new Set();
  let selectedId = null;
  let summaryViewMode = 'summary';
  let importantKeys = new Set();
  let senderInterest = {};
  let busyCalendarEmailId = null;


  function clampVolume(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return 1;
    return Math.min(1, Math.max(0, n));
  }

  function loadAlertVolume() {
    const stored = localStorage.getItem(VOLUME_KEY);
    if (stored == null) return 1;
    return clampVolume(parseFloat(stored));
  }

  let alertVolume = loadAlertVolume();

  function applyAlertVolume(value) {
    alertVolume = clampVolume(value);
    localStorage.setItem(VOLUME_KEY, String(alertVolume));
    if (volumeSlider) {
      volumeSlider.value = String(Math.round(alertVolume * 100));
    }
    if (window.smartinboxSetAlertVolume) {
      window.smartinboxSetAlertVolume(alertVolume);
    }
  }

  if (volumeSlider) {
    volumeSlider.value = String(Math.round(alertVolume * 100));
    volumeSlider.addEventListener('input', () => {
      applyAlertVolume(Number(volumeSlider.value) / 100);
    });
  }

  function fmtTime(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString();
  }

  function normalizeSender(sender) {
    const text = String(sender || '').trim();
    const m = text.match(/<([^>]+)>/);
    if (m) return m[1].trim().toLowerCase();
    if (text.includes('@')) return text.toLowerCase();
    return text.toLowerCase();
  }

  function isImportantSender(sender) {
    return importantKeys.has(normalizeSender(sender));
  }

  function senderScore(sender) {
    const key = normalizeSender(sender);
    return Number(senderInterest[key]?.score) || 0;
  }

  function senderLastVote(sender) {
    const key = normalizeSender(sender);
    const vote = senderInterest[key]?.last_vote;
    return vote === 'up' || vote === 'down' ? vote : null;
  }

  async function voteSender(emailId, vote) {
    const res = await fetch(`/api/emails/${encodeURIComponent(emailId)}/vote`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vote }),
    });
    const data = await res.json();
    if (!data.ok) {
      appendLog({
        ts: new Date().toLocaleTimeString(),
        level: 'error',
        message: data.error || 'Vote failed',
      });
      return;
    }
    if (data.interest) {
      senderInterest[data.interest.sender_key] = data.interest;
    }
    renderInbox();
    const label = vote === 'up' ? 'Upvoted' : 'Downvoted';
    appendLog({
      ts: new Date().toLocaleTimeString(),
      level: 'info',
      message: `${label} ${data.interest?.display || 'sender'} (score ${data.interest?.score ?? 0})`,
    });
  }

  function providerLabel(provider) {
    if (provider === 'proton') return 'Proton';
    if (provider === 'gmail') return 'Gmail';
    return provider || 'Mail';
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

  function isMiddlemanLog(entry) {
    const lvl = String(entry?.level || '').toLowerCase();
    if (lvl === 'middleman') return true;
    const msg = String(entry?.message || '');
    return (
      /middleman/i.test(msg) ||
      /third-party recruiter/i.test(msg) ||
      /Suspected third-party/i.test(msg) ||
      /Foreign middleman/i.test(msg)
    );
  }

  function buildLogElement(entry) {
    const div = document.createElement('div');
    const rawLvl = String(entry.level || 'info');
    const lvl = rawLvl.replace('warning', 'warn');
    const isInboxCheck = String(entry.message || '').startsWith('Inbox check');
    const middleman = isMiddlemanLog(entry);
    let cls = 'activity-entry';
    if (isInboxCheck) cls += ' activity-entry--success';
    if (middleman) cls += ' activity-entry--middleman';
    div.className = cls;
    const tsClass = isInboxCheck ? 'success' : middleman ? 'middleman' : lvl;
    div.innerHTML = `<span class="lvl-${tsClass}">[${entry.ts}]</span> ${escapeHtml(entry.message)}`;
    return div;
  }

  function renderActivityLog(entries) {
    activityLog.innerHTML = '';
    [...(entries || [])]
      .sort(compareLogsDesc)
      .slice(0, 120)
      .forEach((entry) => {
        activityLog.appendChild(buildLogElement(entry));
      });
  }

  function appendLog(entry) {
    const div = buildLogElement(entry);
    activityLog.prepend(div);
    while (activityLog.children.length > 120) {
      activityLog.removeChild(activityLog.lastChild);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
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

  function linkifyPlainText(text) {
    // Turn bare URLs into safe <a> tags after escaping surrounding text.
    const raw = String(text || '');
    if (!raw) return '';
    const urlRe = /(https?:\/\/[^\s<>"'`]+|www\.[^\s<>"'`]+)/gi;
    let out = '';
    let last = 0;
    let match;
    while ((match = urlRe.exec(raw)) !== null) {
      out += escapeHtml(raw.slice(last, match.index));
      let url = match[0];
      // Keep trailing sentence punctuation outside the link.
      let trailing = '';
      while (/[.,);:!?\]]$/.test(url)) {
        trailing = url.slice(-1) + trailing;
        url = url.slice(0, -1);
      }
      const href = /^www\./i.test(url) ? `https://${url}` : url;
      if (/^https?:\/\//i.test(href)) {
        out +=
          `<a class="original-email-link" href="${escapeHtml(href)}" ` +
          `target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>`;
      } else {
        out += escapeHtml(url);
      }
      out += escapeHtml(trailing);
      last = match.index + match[0].length;
    }
    out += escapeHtml(raw.slice(last));
    return out;
  }

  function plainTextToEmailHtml(text) {
    const normalized = String(text || '').replace(/\r\n/g, '\n').trim();
    if (!normalized) return '<p>(empty message)</p>';
    return normalized
      .split(/\n{2,}/)
      .map((block) => {
        const lines = block
          .split('\n')
          .map((line) => linkifyPlainText(line))
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

  function isStarred(email) {
    return Boolean(email && (email.starred === 1 || email.starred === true));
  }

  function isSpamLikely(email) {
    return Boolean(email && (email.is_spam === 1 || email.is_spam === true));
  }

  async function starEmail(emailId) {
    const res = await fetch(`/api/emails/${encodeURIComponent(emailId)}/star`, {
      method: 'POST',
    });
    const data = await res.json();
    if (!data.ok) {
      appendLog({
        ts: new Date().toLocaleTimeString(),
        level: 'error',
        message: data.error || 'Could not star email',
      });
      return;
    }
    const row = emails.find((e) => e.id === emailId);
    if (row && data.email) {
      Object.assign(row, data.email);
    }
    if (data.starred && data.sender) {
      importantKeys.add(data.sender.sender_key);
    }
    renderInbox();
    appendLog({
      ts: new Date().toLocaleTimeString(),
      level: data.starred ? 'success' : 'info',
      message: data.starred
        ? `Starred: ${row?.subject || 'email'}`
        : `Unstarred: ${row?.subject || 'email'}`,
    });
  }

  function emailSortKey(e) {
    const received = Number(e?.received_at);
    const created = Number(e?.created_at);
    if (Number.isFinite(received) && received > 0) return received;
    if (Number.isFinite(created) && created > 0) return created;
    return 0;
  }

  function emailVoteTier(email) {
    const vote = senderLastVote(email?.sender);
    return vote === 'down' ? 1 : 0;
  }

  function sortEmailsForInbox(list) {
    return [...(list || [])].sort((a, b) => {
      const tierA = emailVoteTier(a);
      const tierB = emailVoteTier(b);
      if (tierA !== tierB) return tierA - tierB;
      return emailSortKey(b) - emailSortKey(a);
    });
  }

  function buildOriginalEmailHtml(row) {
    const from = row.sender || '(unknown)';
    const subject = row.subject || '(no subject)';
    const date = fmtTime(row.received_at);
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

  function loadHiddenInbox() {
    hiddenInboxIds = new Set();
    try {
      const raw = localStorage.getItem(INBOX_HIDDEN_KEY);
      if (!raw) return;
      const ids = JSON.parse(raw);
      if (Array.isArray(ids)) {
        hiddenInboxIds = new Set(ids.map(String));
      }
    } catch (_) { /* ignore */ }
  }

  function saveHiddenInbox() {
    try {
      localStorage.setItem(INBOX_HIDDEN_KEY, JSON.stringify([...hiddenInboxIds]));
    } catch (_) { /* ignore */ }
  }

  function pruneHiddenInbox() {
    const emailIds = new Set(emails.map((e) => String(e.id)));
    let changed = false;
    for (const id of hiddenInboxIds) {
      if (!emailIds.has(id)) {
        hiddenInboxIds.delete(id);
        changed = true;
      }
    }
    if (changed) saveHiddenInbox();
  }

  function hideInboxEmail(id) {
    const idStr = String(id);
    const visibleBefore = sortedInboxEmails();
    const wasSelected = selectedId === id;
    hiddenInboxIds.add(idStr);
    saveHiddenInbox();
    if (wasSelected) {
      const index = visibleBefore.findIndex((e) => String(e.id) === idStr);
      const remaining = visibleBefore.filter((e) => String(e.id) !== idStr);
      const next = remaining[index] || remaining[index - 1];
      if (next) {
        selectEmail(next.id);
        return;
      }
      selectedId = null;
      btnResummarize.disabled = true;
      showSummaryMessage('Select an email to view its Ollama summary.', '');
      updateViewSwitcher();
    }
    renderInbox();
  }

  function hideAllInboxEmails() {
    const visible = sortedInboxEmails();
    if (!visible.length) return;
    let added = 0;
    for (const row of visible) {
      const idStr = String(row.id);
      if (!hiddenInboxIds.has(idStr)) {
        hiddenInboxIds.add(idStr);
        added += 1;
      }
    }
    if (!added) return;
    saveHiddenInbox();
    selectedId = null;
    btnResummarize.disabled = true;
    showSummaryMessage('Select an email to view its Ollama summary.', '');
    updateViewSwitcher();
    renderInbox();
    appendLog({
      ts: new Date().toLocaleTimeString(),
      level: 'info',
      message: `Inbox — hid ${added} email${added === 1 ? '' : 's'} from view`,
    });
  }

  function sortedInboxEmails() {
    return sortEmailsForInbox(emails).filter((e) => !hiddenInboxIds.has(String(e.id)));
  }

  function setInboxKeyboardNav(active) {
    document.body.classList.toggle('inbox-keyboard-nav', !!active);
  }

  function scrollSelectedInboxItemIntoView() {
    requestAnimationFrame(() => {
      const selected = inboxList?.querySelector('.email-item.selected');
      selected?.scrollIntoView({ block: 'nearest' });
      if (document.body.classList.contains('inbox-keyboard-nav') && selected) {
        if (selected instanceof HTMLElement) selected.focus({ preventScroll: true });
      }
    });
  }

  function calendarButtonLabel(email) {
    if (email.calendar_event_count > 0) {
      return `Re-scan (${email.calendar_event_count})`;
    }
    if (email.calendar_extracted) {
      return 'Re-scan';
    }
    return 'Add to calendar';
  }

  async function addToCalendar(emailId) {
    if (!emailId || busyCalendarEmailId) return;
    const row = emails.find((e) => e.id === emailId);
    const subject = row?.subject || '(no subject)';
    const rescan = !!(row?.calendar_extracted || (row?.calendar_event_count || 0) > 0);
    busyCalendarEmailId = emailId;
    renderInbox();
    // Server owns the activity log for the full pipeline; only a short local
    // kickoff line so the UI reacts immediately before SSE arrives.
    appendLog({
      ts: new Date().toLocaleTimeString(),
      level: 'info',
      message: rescan
        ? `Re-scan requested (spam → middleman → calendar) — ${subject}`
        : `Scan requested (spam → middleman → calendar) — ${subject}`,
    });
    try {
      const res = await fetch(`/api/emails/${encodeURIComponent(emailId)}/calendar`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: true }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Calendar extraction failed');
      const count = data.events_found || 0;
      if (row) {
        row.calendar_extracted = true;
        row.calendar_event_count = count;
        if (Array.isArray(data.tags)) row.tags = data.tags;
        if (Array.isArray(data.tag_entries)) row.tag_entries = data.tag_entries;
        if (data.is_spam === true || data.is_spam === false) {
          row.is_spam = data.is_spam ? 1 : 0;
        }
      }
      // Prefer server activity log (includes spam + middleman + calendar steps).
      if (Array.isArray(data.logs) && data.logs.length) {
        renderActivityLog(data.logs);
      } else {
        appendLog({
          ts: new Date().toLocaleTimeString(),
          level: data.error ? 'warning' : 'info',
          message: data.message || `Re-scan finished — ${subject}`,
        });
      }
    } catch (e) {
      appendLog({
        ts: new Date().toLocaleTimeString(),
        level: 'warning',
        message: `Re-scan failed — ${subject}: ${e}`,
      });
    } finally {
      busyCalendarEmailId = null;
      renderInbox();
    }
  }

  function navigateInbox(delta) {
    const sorted = sortedInboxEmails();
    if (!sorted.length) return;
    setInboxKeyboardNav(true);
    let index = sorted.findIndex((e) => e.id === selectedId);
    if (index < 0) {
      index = delta > 0 ? -1 : sorted.length;
    }
    const next = index + delta;
    if (next < 0 || next >= sorted.length) return;
    selectEmail(sorted[next].id, { scrollInbox: true, keyboard: true });
  }

  function renderInbox() {
    if (!emails.length) {
      inboxList.innerHTML = demoMode
        ? '<p class="summary-empty">Demo inbox is empty. Turn demo mode off and on in Settings to restore samples.</p>'
        : '<p class="summary-empty">No emails yet. Connect Gmail or Proton in Settings.</p>';
      inboxCount.textContent = demoMode ? 'DEMO · 0 sample emails' : '0 emails';
      return;
    }
    const total = sortEmailsForInbox(emails).length;
    const sorted = sortedInboxEmails();
    const hiddenCount = total - sorted.length;
    const countLabel = sorted.length === 1 ? 'email' : 'emails';
    let countText = demoMode
      ? `DEMO · ${sorted.length} sample ${countLabel}`
      : `${sorted.length} ${countLabel}`;
    if (hiddenCount > 0) countText += ` (${hiddenCount} hidden)`;
    inboxCount.textContent = countText;
    if (!sorted.length) {
      inboxList.innerHTML =
        '<p class="summary-empty">All emails are hidden from this view. New mail will still appear.</p>';
      return;
    }
    inboxList.innerHTML = sorted
      .map((e) => {
        const sel = e.id === selectedId ? ' selected' : '';
        const starred = isStarred(e);
        const score = senderScore(e.sender);
        const junk = score < 0 ? ' junk' : '';
        const spam = isSpamLikely(e) ? ' spam-likely' : '';
        const imp = starred || isImportantSender(e.sender) ? ' important' : '';
        const badge = starred
          ? '<span class="star-badge" title="Starred">★</span> '
          : '<span class="star-badge star-badge-empty" title="Double-click to star">☆</span> ';
        const prov = e.provider
          ? `<span class="provider-badge provider-${escapeHtml(e.provider)}">${escapeHtml(providerLabel(e.provider))}</span> `
          : '';
        const lastVote = senderLastVote(e.sender);
        const upActive = lastVote === 'up' ? ' vote-active' : '';
        const downActive = lastVote === 'down' ? ' vote-active' : '';
        const calBusy = busyCalendarEmailId === e.id;
        const calBadge =
          e.calendar_event_count > 0
            ? `<span class="inbox-cal-badge" title="${e.calendar_event_count} calendar event${e.calendar_event_count === 1 ? '' : 's'}">${e.calendar_event_count} on calendar</span> `
            : '';
        const tagEntries = Array.isArray(e.tag_entries)
          ? e.tag_entries
          : (Array.isArray(e.tags) ? e.tags.map((id) => ({ id, label: String(id).replace(/_/g, ' ') })) : []);
        const tagBadges = tagEntries
          .map((t) => {
            const id = escapeHtml(t.id || '');
            // Plain text + flag image (Twemoji SVG) — emoji fonts are often missing on Linux.
            const flagRaw = t.flag || t.country_flag || '';
            const flagImg = t.flag_img || '';
            let textRaw = t.label_text || '';
            if (!textRaw) {
              textRaw = String(t.label || t.id || 'tag').replace(/\s*[\u{1F1E6}-\u{1F1FF}]{2}\s*$/u, '').trim();
            }
            if (!textRaw) textRaw = String(t.id || 'tag').replace(/_/g, ' ');
            const text = escapeHtml(textRaw);
            let flagHtml = '';
            if (flagImg) {
              flagHtml = `<img class="tag-flag-img" src="${escapeHtml(flagImg)}" alt="${escapeHtml(flagRaw || 'flag')}" width="14" height="14" loading="lazy" decoding="async">`;
            } else if (flagRaw) {
              flagHtml = `<span class="tag-flag" aria-hidden="true">${flagRaw}</span>`;
            }
            const title = escapeHtml(t.title || `${textRaw}${flagRaw ? ' ' + flagRaw : ''}`);
            const isForeignTag =
              t.id === 'foreign_middleman' || String(t.id || '').startsWith('foreign_middleman_');
            const clickable =
              t.clickable ||
              t.id === 'possible_indian_middleman' ||
              isForeignTag;
            const confirmAction =
              t.confirm_action ||
              (t.id === 'possible_indian_middleman' ? 'indian' : isForeignTag ? 'foreign' : '');
            const countryCode = escapeHtml(t.country_code || '');
            const inner = `${text}${flagHtml ? ` ${flagHtml}` : ''}`;
            if (clickable && !demoMode && confirmAction) {
              const aria =
                confirmAction === 'foreign'
                  ? 'Save Foreign Middleman to database'
                  : 'Confirm as Indian Middleman';
              return `<button type="button" class="email-tag-badge tag-${id} tag-clickable" data-tag-action="confirm-middleman" data-confirm-kind="${escapeHtml(confirmAction)}" data-country-code="${countryCode}" title="${title}" aria-label="${aria}">${inner}</button> `;
            }
            return `<span class="email-tag-badge tag-${id}" title="${title}">${inner}</span> `;
          })
          .join('');
        const hasPossible = tagEntries.some((t) => t.id === 'possible_indian_middleman');
        const hasConfirmed = tagEntries.some((t) => t.id === 'indian_middleman');
        const hasForeign = tagEntries.some(
          (t) => t.id === 'foreign_middleman' || String(t.id || '').startsWith('foreign_middleman_'),
        );
        let middleman = '';
        if (hasConfirmed) middleman += ' middleman-confirmed';
        if (hasPossible) middleman += ' middleman-suspect';
        if (hasForeign) middleman += ' foreign-middleman';
        return `<div class="email-item${sel}${imp}${starred ? ' starred' : ''}${junk}${spam}${middleman}" data-id="${escapeHtml(e.id)}" tabindex="-1">
          <div class="email-votes" role="group" aria-label="Rate sender">
            <button type="button" class="vote-btn vote-up${upActive}" data-vote="up" title="Interested in this sender" aria-label="Upvote sender">▲</button>
            <button type="button" class="vote-btn vote-down${downActive}" data-vote="down" title="Mark sender as junk" aria-label="Downvote sender">▼</button>
          </div>
          <div class="email-item-body">
            <div class="email-subject">${badge}${calBadge}${prov}${tagBadges}${escapeHtml(e.subject || '(no subject)')}</div>
            <div class="email-meta">${escapeHtml(e.sender || '')} · ${fmtTime(e.received_at)}</div>
          </div>
          <div class="email-item-actions">
            <button type="button" class="btn btn-primary btn-small btn-add-calendar" title="Extract dates and add to calendar" ${calBusy || demoMode ? 'disabled' : ''}>${escapeHtml(calendarButtonLabel(e))}</button>
            <button type="button" class="btn btn-secondary btn-small btn-hide-email" title="Hide from inbox">Hide</button>
          </div>
        </div>`;
      })
      .join('');
    inboxList.querySelectorAll('.email-item').forEach((el) => {
      el.addEventListener('click', (ev) => {
        if (ev.target.closest('.vote-btn, .btn-hide-email, .btn-add-calendar, .tag-clickable')) return;
        setInboxKeyboardNav(false);
        selectEmail(el.dataset.id);
      });
      el.addEventListener('dblclick', (ev) => {
        if (ev.target.closest('.vote-btn, .btn-hide-email, .btn-add-calendar, .tag-clickable')) return;
        ev.preventDefault();
        ev.stopPropagation();
        starEmail(el.dataset.id);
      });
      el.querySelectorAll('.vote-btn').forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          voteSender(el.dataset.id, btn.dataset.vote);
        });
      });
      el.querySelector('.btn-add-calendar')?.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        addToCalendar(el.dataset.id);
      });
      el.querySelector('.btn-hide-email')?.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        hideInboxEmail(el.dataset.id);
      });
      el.querySelectorAll('[data-tag-action="confirm-middleman"]').forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          confirmMiddleman(el.dataset.id, {
            kind: btn.dataset.confirmKind || '',
            countryCode: btn.dataset.countryCode || '',
          });
        });
      });
    });
  }

  async function confirmMiddleman(emailId, opts = {}) {
    if (!emailId || demoMode) return;
    const row = emails.find((e) => e.id === emailId);
    const subject = row?.subject || '(no subject)';
    const kind = opts.kind || '';
    const countryCode = opts.countryCode || '';
    const label =
      kind === 'foreign'
        ? `Foreign Middleman${countryCode ? ' (' + countryCode + ')' : ''}`
        : 'Indian Middleman';
    appendLog({
      ts: new Date().toLocaleTimeString(),
      level: 'middleman',
      message: `Saving ${label} to middlemen database — ${subject}`,
    });
    try {
      const res = await fetch(
        `/api/emails/${encodeURIComponent(emailId)}/confirm-middleman`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: kind || undefined,
            country_code: countryCode || undefined,
          }),
        },
      );
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Confirm failed');
      if (Array.isArray(data.logs) && data.logs.length) {
        renderActivityLog(data.logs);
      } else {
        const count = data.tagged_count || 1;
        const scope = data.auto_domain
          ? `domain @${data.domain || '?'}`
          : `sender ${data.email_address || ''}`;
        appendLog({
          ts: new Date().toLocaleTimeString(),
          level: 'middleman',
          message: `${label} saved (${count} messages, ${scope}) — ${subject}`,
        });
      }
      // Optimistic local update for the clicked row; SSE will refresh the list.
      if (row) {
        const tags = Array.isArray(data.tags)
          ? data.tags
          : kind === 'foreign'
            ? [data.tag_id || 'foreign_middleman']
            : ['indian_middleman'];
        row.tags = tags;
        row.tag_entries = Array.isArray(data.tag_entries)
          ? data.tag_entries
          : publicTagEntriesFallback(tags);
      }
      renderInbox();
    } catch (e) {
      appendLog({
        ts: new Date().toLocaleTimeString(),
        level: 'warning',
        message: `Confirm middleman failed — ${subject}: ${e}`,
      });
    }
  }

  function publicTagEntriesFallback(tags) {
    return (tags || []).map((id) => ({
      id,
      label: String(id).replace(/_/g, ' '),
      label_text: String(id).replace(/_/g, ' '),
    }));
  }

  function populateThemeSelect() {
    if (!summaryTheme) return;
    summaryTheme.innerHTML = THEME_OPTIONS.map(
      ({ value, label }) =>
        `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`
    ).join('');
  }

  function applySummaryTheme(theme) {
    if (theme === 'minecraft') theme = 'mailcraft';
    const chosen = THEMES.includes(theme) ? theme : 'modern';
    if (summaryTheme) summaryTheme.value = chosen;
    if (summaryViewport) {
      summaryViewport.className = `summary-viewport theme-${chosen}`;
    }
    try {
      localStorage.setItem(THEME_KEY, chosen);
    } catch (_) { /* ignore */ }
  }

  function showSummaryMarkdown(text) {
    summaryBody.className = 'summary-body';
    summaryBody.innerHTML = `<div class="markdown-body">${renderMarkdown(text)}</div>`;
  }

  function showSummaryMessage(text, kind) {
    summaryViewMode = 'summary';
    updateViewSwitcher();
    summaryBody.className = `summary-body${kind ? ` summary-${kind}` : ''}`;
    summaryBody.textContent = text;
  }

  function sourceProviderLabel(provider) {
    const key = String(provider || '').toLowerCase();
    if (key === 'proton') return 'Proton Mail';
    if (key === 'gmail') return 'Gmail';
    return 'mail';
  }

  function selectedEmailSourceUrl() {
    if (!selectedId) return null;
    const row = emails.find((e) => e.id === selectedId);
    const url = row?.source_url;
    return url ? String(url) : null;
  }

  function openSelectedEmailSource() {
    const url = selectedEmailSourceUrl();
    if (!url || demoMode) return;
    window.open(url, '_blank', 'noopener,noreferrer');
  }

  function updateViewSwitcher() {
    if (!summaryViewOptions.length) return;
    const hasSelection = !!selectedId;
    const row = emails.find((e) => e.id === selectedId);
    const sourceUrl = row?.source_url;
    summaryViewOptions.forEach((btn) => {
      const view = btn.dataset.view;
      const active = hasSelection && summaryViewMode === view;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      btn.disabled = !hasSelection;
    });
    document.querySelectorAll('.btn-go-source').forEach((btn) => {
      const enabled = hasSelection && !!sourceUrl && !demoMode;
      btn.disabled = !enabled;
      btn.title = enabled
        ? `Open this message in ${sourceProviderLabel(row?.provider)}`
        : demoMode
          ? 'Not available in demo mode'
          : 'Open in Gmail or Proton (needs Message-ID from a future poll)';
    });
  }

  function showOriginalEmail(row) {
    summaryBody.className = 'summary-body original-email-view';
    summaryBody.innerHTML = buildOriginalEmailHtml(row);
  }

  function renderSummaryPanel() {
    if (!selectedId) return;
    const row = emails.find((e) => e.id === selectedId);
    if (!row) return;
    if (summaryViewMode === 'original') {
      showOriginalEmail(row);
      return;
    }
    const text = (row.summary_detailed || row.summary_short || '').trim();
    if (text) {
      showSummaryMarkdown(text);
    } else {
      summaryBody.className = 'summary-body';
      summaryBody.innerHTML =
        '<div class="markdown-body"><p class="summary-empty">(no summary yet)</p></div>';
    }
  }

  function selectEmail(id, options = {}) {
    selectedId = id;
    if (options.keyboard) {
      setInboxKeyboardNav(true);
    }
    if (options.summaryView !== 'original') {
      summaryViewMode = 'summary';
    }
    renderInbox();
    renderSummaryPanel();
    btnResummarize.disabled = false;
    updateViewSwitcher();
    if (options.scrollInbox) {
      scrollSelectedInboxItemIntoView();
    }
  }

  function applySnapshot(snap) {
    demoMode = !!snap.demo_mode;
    emails = sortEmailsForInbox(snap.emails || []);
    pruneHiddenInbox();
    importantKeys = new Set(snap.important_sender_keys || []);
    senderInterest = snap.sender_interest || {};
    renderInbox();
    if (selectedId) selectEmail(selectedId);
    renderActivityLog(snap.logs || []);
    const mail = snap.mail_accounts || {};
    const accounts = mail.accounts || [];
    const demoPrefix = demoMode ? 'DEMO MODE · ' : '';
    if (accounts.length) {
      const names = accounts
        .map((a) => `${providerLabel(a.provider)}: ${a.email}`)
        .join(' · ');
      connStatus.textContent =
        `${demoPrefix}${names} · poll ${snap.poll_interval}s · cooldown ${snap.alert_cooldown}s · important: ${snap.important_alert_mode || 'always'}`;
      connStatus.className = demoMode ? 'status-line warn' : 'status-line';
    } else {
      connStatus.textContent = demoMode
        ? 'DEMO MODE · sample inbox — no live mail shown'
        : 'No mail accounts connected — open Settings';
      connStatus.className = 'status-line warn';
    }
  }

  function connectSSE() {
    const es = new EventSource('/api/stream');
    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'snapshot') applySnapshot(msg.data);
        if (msg.type === 'log') appendLog(msg.data);
        if (msg.type === 'logs') renderActivityLog(msg.data || []);
        if (msg.type === 'emails') {
          emails = sortEmailsForInbox(msg.data || []);
          pruneHiddenInbox();
          if (selectedId && !emails.some((e) => e.id === selectedId)) {
            selectedId = null;
            btnResummarize.disabled = true;
            showSummaryMessage('Select an email to view its Ollama summary.', '');
            updateViewSwitcher();
          }
          renderInbox();
          if (selectedId) renderSummaryPanel();
        }
        if (msg.type === 'important_senders') {
          importantKeys = new Set((msg.data || []).map((s) => s.sender_key));
          renderInbox();
        }
        if (msg.type === 'sender_interest') {
          senderInterest = msg.data || {};
          renderInbox();
        }

      } catch (_) { /* ignore */ }
    };
    es.onerror = () => {
      connStatus.textContent = 'SSE disconnected — retrying…';
      connStatus.className = 'status-line error';
    };
  }

  btnPoll.addEventListener('click', async () => {
    appendLog({
      ts: new Date().toLocaleTimeString(),
      level: 'info',
      message: 'Inbox check — Check now clicked, starting…',
    });
    btnPoll.disabled = true;
    try {
      const res = await fetch('/api/poll', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ detailed: true }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        appendLog({
          ts: new Date().toLocaleTimeString(),
          level: 'error',
          message: data.error || 'Inbox check — request failed',
        });
      }
    } catch (e) {
      appendLog({
        ts: new Date().toLocaleTimeString(),
        level: 'error',
        message: `Inbox check — request failed: ${e}`,
      });
    } finally {
      btnPoll.disabled = false;
    }
  });

  function isActivityLogHidden() {
    try {
      return localStorage.getItem(ACTIVITY_LOG_HIDDEN_KEY) === '1';
    } catch (_) {
      return false;
    }
  }

  function setActivityLogHidden(hidden) {
    document.body.classList.toggle('activity-log-hidden', !!hidden);
    if (activityLogPanel) {
      activityLogPanel.hidden = !!hidden;
      activityLogPanel.setAttribute('aria-hidden', hidden ? 'true' : 'false');
    }
    if (btnShowActivityLog) {
      btnShowActivityLog.hidden = !hidden;
    }
    try {
      localStorage.setItem(ACTIVITY_LOG_HIDDEN_KEY, hidden ? '1' : '0');
    } catch (_) { /* ignore */ }
  }

  // Restore collapse preference before first paint-heavy work.
  setActivityLogHidden(isActivityLogHidden());

  if (btnHideActivityLog) {
    btnHideActivityLog.addEventListener('click', () => {
      setActivityLogHidden(true);
    });
  }
  if (btnShowActivityLog) {
    btnShowActivityLog.addEventListener('click', () => {
      setActivityLogHidden(false);
    });
  }

  if (btnClearActivityLog) {
    btnClearActivityLog.addEventListener('click', async () => {
      btnClearActivityLog.disabled = true;
      try {
        await fetch('/api/logs/clear', { method: 'POST' });
        renderActivityLog([]);
      } finally {
        btnClearActivityLog.disabled = false;
      }
    });
  }

  if (btnEmptyInbox) {
    btnEmptyInbox.addEventListener('click', () => {
      if (!sortedInboxEmails().length) return;
      if (!window.confirm('Hide all emails from the inbox display?')) return;
      hideAllInboxEmails();
    });
  }

  summaryViewOptions.forEach((btn) => {
    btn.addEventListener('click', () => {
      if (!selectedId) return;
      const view = btn.dataset.view;
      if (view !== 'summary' && view !== 'original') return;
      if (summaryViewMode === view) return;
      summaryViewMode = view;
      updateViewSwitcher();
      renderSummaryPanel();
    });
  });

  document.querySelectorAll('.btn-go-source').forEach((btn) => {
    btn.addEventListener('click', () => {
      openSelectedEmailSource();
    });
  });

  btnResummarize.addEventListener('click', async () => {
    if (!selectedId) return;
    btnResummarize.disabled = true;
    showSummaryMessage('Summarizing…', 'loading');
    try {
      const res = await fetch(`/api/summarize/${encodeURIComponent(selectedId)}`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        showSummaryMarkdown(data.summary);
        const row = emails.find((e) => e.id === selectedId);
        if (row) {
          row.summary_detailed = data.summary;
          row.summary_short = data.summary.slice(0, 500);
        }
      } else {
        showSummaryMessage(data.error || 'Summary failed', 'error');
      }
    } finally {
      btnResummarize.disabled = false;
    }
  });

  if (summaryTheme) {
    populateThemeSelect();
    let savedTheme = 'modern';
    try {
      savedTheme = localStorage.getItem(THEME_KEY) || 'modern';
    } catch (_) { /* ignore */ }
    applySummaryTheme(savedTheme);
    summaryTheme.addEventListener('change', () => {
      applySummaryTheme(summaryTheme.value);
    });
  }

  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowUp' && ev.key !== 'ArrowDown') return;
    const active = document.activeElement;
    const tag = active?.tagName?.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (active?.isContentEditable) return;
    ev.preventDefault();
    navigateInbox(ev.key === 'ArrowDown' ? 1 : -1);
  });

  inboxList?.addEventListener('mousedown', () => {
    setInboxKeyboardNav(false);
  });

  loadHiddenInbox();
  connectSSE();
})();