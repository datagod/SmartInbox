(function () {
  const DISMISS_KEY = 'smartinbox-search-dismissed';

  const searchForm = document.getElementById('search-form');
  const searchQuery = document.getElementById('search-query');
  const searchStatus = document.getElementById('search-status');
  const searchResults = document.getElementById('search-results');
  const btnClear = document.getElementById('btn-clear-search');
  const searchActivityLog = document.getElementById('search-activity-log');
  const btnClearSearchActivityLog = document.getElementById('btn-clear-search-activity-log');
  let currentQuery = '';
  let dismissed = new Set();
  let busyEmailId = null;
  let selectedEmailId = null;
  let expandedEmailHtml = null;
  let lastResults = [];
  let lastDemoMode = false;
  let lastTotal = 0;

  const PROVIDER_LABELS = { gmail: 'Gmail', proton: 'Proton' };

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
      msg.startsWith('New email (') ||
      msg.startsWith('Calendar')
    );
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
    if (!searchActivityLog) return;
    searchActivityLog.innerHTML = '';
    [...(entries || [])]
      .sort(compareLogsDesc)
      .slice(0, 120)
      .forEach((entry) => {
        searchActivityLog.appendChild(buildLogElement(entry));
      });
  }

  function appendActivityLog(entry) {
    if (!searchActivityLog || !entry) return;
    searchActivityLog.prepend(buildLogElement(entry));
    while (searchActivityLog.children.length > 120) {
      searchActivityLog.removeChild(searchActivityLog.lastChild);
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

  function providerLabel(key) {
    const k = String(key || '').toLowerCase();
    return PROVIDER_LABELS[k] || (k ? k.charAt(0).toUpperCase() + k.slice(1) : 'Mail');
  }

  function formatTs(ts) {
    const n = Number(ts);
    if (!n) return '—';
    return new Date(n * 1000).toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  function formatEmailTime(ts) {
    const n = Number(ts);
    if (!n) return '—';
    return new Date(n * 1000).toLocaleString();
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
    const raw = String(text || '');
    if (!raw) return '';
    const urlRe = /(https?:\/\/[^\s<>"'`]+|www\.[^\s<>"'`]+)/gi;
    let out = '';
    let last = 0;
    let match;
    while ((match = urlRe.exec(raw)) !== null) {
      out += escapeHtml(raw.slice(last, match.index));
      let url = match[0];
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

  function buildSourceEmailHtml(row) {
    const from = row.sender || '(unknown)';
    const subject = row.subject || '(no subject)';
    const date = formatEmailTime(row.received_at || row.created_at);
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
    searchResults?.querySelectorAll('.inline-email-expand').forEach((el) => el.remove());
  }

  function findSearchAnchor(emailId) {
    if (!searchResults || !emailId) return null;
    return searchResults.querySelector(`.search-result[data-id="${CSS.escape(emailId)}"]`);
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

  function markSelectedEmail(emailId) {
    selectedEmailId = emailId || null;
    searchResults?.querySelectorAll('.search-result.is-selected').forEach((el) => {
      el.classList.remove('is-selected');
    });
    if (!emailId || !searchResults) return;
    const el = searchResults.querySelector(`[data-id="${CSS.escape(emailId)}"]`);
    if (el) el.classList.add('is-selected');
  }

  async function loadSourceEmailInto(bodyEl, emailId) {
    if (!bodyEl || !emailId) return;
    bodyEl.innerHTML = '<p class="search-empty">Loading original email…</p>';
    try {
      const res = await fetch(`/api/emails/${encodeURIComponent(emailId)}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Email not found');
      expandedEmailHtml = buildSourceEmailHtml(data.email);
      bodyEl.innerHTML = expandedEmailHtml;
    } catch (e) {
      expandedEmailHtml = null;
      bodyEl.innerHTML = `<p class="search-empty">Could not load email: ${escapeHtml(String(e))}</p>`;
    }
  }

  function restoreInlineExpansion(emailId) {
    if (!emailId) return;
    const anchor = findSearchAnchor(emailId);
    if (!anchor) return;
    removeInlineExpansions();
    const expand = createInlineExpandShell('Original email');
    expand.dataset.forId = emailId;
    anchor.insertAdjacentElement('afterend', expand);
    anchor.classList.add('is-expanded');
    const bodyEl = expand.querySelector('.inline-email-expand-body');
    if (expandedEmailHtml && selectedEmailId === emailId) {
      bodyEl.innerHTML = expandedEmailHtml;
    } else {
      loadSourceEmailInto(bodyEl, emailId);
    }
  }

  async function showSourceEmail(emailId) {
    if (!emailId) return;
    if (selectedEmailId === emailId) {
      closeSourceEmail();
      return;
    }
    selectedEmailId = emailId;
    markSelectedEmail(emailId);
    removeInlineExpansions();
    const anchor = findSearchAnchor(emailId);
    if (!anchor) return;
    const expand = createInlineExpandShell('Original email');
    expand.dataset.forId = emailId;
    anchor.classList.add('is-expanded');
    anchor.insertAdjacentElement('afterend', expand);
    const bodyEl = expand.querySelector('.inline-email-expand-body');
    expand.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    await loadSourceEmailInto(bodyEl, emailId);
  }

  function closeSourceEmail() {
    selectedEmailId = null;
    expandedEmailHtml = null;
    removeInlineExpansions();
    markSelectedEmail(null);
    searchResults?.querySelectorAll('.search-result.is-expanded').forEach((el) => {
      el.classList.remove('is-expanded');
    });
  }

  function dismissStorageKey(query) {
    return `${DISMISS_KEY}:${query.trim().toLowerCase()}`;
  }

  function loadDismissed(query) {
    dismissed = new Set();
    if (!query) return;
    try {
      const raw = sessionStorage.getItem(dismissStorageKey(query));
      if (!raw) return;
      const ids = JSON.parse(raw);
      if (Array.isArray(ids)) {
        dismissed = new Set(ids.map(String));
      }
    } catch (_) { /* ignore */ }
  }

  function saveDismissed() {
    if (!currentQuery) return;
    try {
      sessionStorage.setItem(
        dismissStorageKey(currentQuery),
        JSON.stringify([...dismissed])
      );
    } catch (_) { /* ignore */ }
  }

  function calendarButtonLabel(email) {
    if (email.calendar_event_count > 0) {
      return `Re-scan calendar (${email.calendar_event_count})`;
    }
    if (email.calendar_extracted) {
      return 'Re-scan calendar';
    }
    return 'Add to calendar';
  }

  function currentPayload() {
    return {
      results: lastResults,
      demo_mode: lastDemoMode,
      total: lastTotal,
    };
  }

  function renderResults(data) {
    if (!searchResults) return;
    lastResults = data.results || [];
    lastDemoMode = !!data.demo_mode;
    lastTotal = data.total ?? lastResults.length;

    const visible = lastResults.filter((row) => !dismissed.has(String(row.id)));
    const hiddenCount = lastResults.length - visible.length;

    if (!currentQuery) {
      searchResults.innerHTML = '<p class="search-empty">Enter a query to search stored mail.</p>';
      return;
    }
    if (!lastResults.length) {
      searchResults.innerHTML = '<p class="search-empty">No stored emails match that search.</p>';
      return;
    }
    if (!visible.length) {
      searchResults.innerHTML =
        '<p class="search-empty">All matches were removed from this view. Clear dismissals or run a new search.</p>';
      return;
    }

    searchResults.innerHTML = visible
      .map((email) => {
        const id = escapeHtml(email.id);
        const subject = escapeHtml(email.subject || '(no subject)');
        const sender = escapeHtml(email.sender || 'unknown sender');
        const provider = escapeHtml(providerLabel(email.provider));
        const account = email.account_email ? escapeHtml(email.account_email) : '';
        const when = escapeHtml(formatTs(email.received_at || email.created_at));
        const preview = escapeHtml(email.preview || email.snippet || '');
        const summary = email.summary_short
          ? `<div class="search-result-summary">${escapeHtml(email.summary_short)}</div>`
          : '';
        const calNote =
          email.calendar_event_count > 0
            ? `<span class="search-result-badge">${email.calendar_event_count} calendar event${email.calendar_event_count === 1 ? '' : 's'}</span>`
            : email.calendar_extracted
              ? '<span class="search-result-badge search-result-badge--muted">Scanned, no dates</span>'
              : '';
        const calBusy = busyEmailId === email.id;
        const selected = selectedEmailId === email.id ? ' is-selected' : '';
        return `<article class="search-result search-result--clickable${selected}" data-id="${id}" role="button" tabindex="0" title="View original email">
          <div class="search-result-main">
          <div class="search-result-head">
            <h3 class="search-result-subject">${subject}</h3>
            ${calNote}
          </div>
          <div class="search-result-meta">${sender} · ${provider}${account ? ` · ${account}` : ''} · ${when}</div>
          ${preview ? `<p class="search-result-preview">${preview}</p>` : ''}
          ${summary}
          </div>
          <div class="search-result-actions">
            <button type="button" class="btn btn-primary btn-small btn-add-calendar" data-id="${id}" ${calBusy || lastDemoMode ? 'disabled' : ''}>${escapeHtml(calendarButtonLabel(email))}</button>
            <button type="button" class="btn btn-secondary btn-small btn-dismiss-result" data-id="${id}">Remove from results</button>
          </div>
          <p class="search-result-action-status" hidden></p>
        </article>`;
      })
      .join('');

    if (searchStatus) {
      const total = lastTotal;
      let line = `${visible.length} shown`;
      if (total !== visible.length) line += ` of ${total} match${total === 1 ? '' : 'es'}`;
      else line += ` match${visible.length === 1 ? '' : 'es'}`;
      if (hiddenCount > 0) line += ` (${hiddenCount} hidden)`;
      if (lastDemoMode) line += ' · Demo mode — calendar extraction disabled';
      searchStatus.textContent = line;
    }

    searchResults.querySelectorAll('.search-result').forEach((article) => {
      const emailId = article.dataset.id;
      const statusEl = article.querySelector('.search-result-action-status');
      const openEmail = () => showSourceEmail(emailId);
      article.querySelector('.search-result-main')?.addEventListener('click', openEmail);
      article.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        if (ev.target.closest('.search-result-actions')) return;
        ev.preventDefault();
        openEmail();
      });
      article.querySelector('.btn-add-calendar')?.addEventListener('click', (ev) => {
        ev.stopPropagation();
        addToCalendar(emailId, statusEl);
      });
      article.querySelector('.btn-dismiss-result')?.addEventListener('click', (ev) => {
        ev.stopPropagation();
        dismissResult(emailId);
      });
    });

    if (selectedEmailId) {
      markSelectedEmail(selectedEmailId);
      restoreInlineExpansion(selectedEmailId);
    }
  }

  async function runSearch(query) {
    currentQuery = (query || '').trim();
    loadDismissed(currentQuery);
    if (!currentQuery) {
      if (searchStatus) searchStatus.textContent = '';
      lastResults = [];
      lastTotal = 0;
      renderResults({ results: [] });
      return;
    }
    if (searchStatus) searchStatus.textContent = 'Searching…';
    try {
      const params = new URLSearchParams({ q: currentQuery, limit: '100' });
      const res = await fetch(`/api/search?${params.toString()}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Search failed');
      renderResults(data);
    } catch (e) {
      if (searchStatus) searchStatus.textContent = `Error: ${e}`;
      if (searchResults) {
        searchResults.innerHTML = '<p class="search-empty">Could not search stored mail.</p>';
      }
    }
  }

  async function addToCalendar(emailId, statusEl) {
    if (!emailId || busyEmailId) return;
    const email = (currentPayload()?.results || []).find((row) => row.id === emailId);
    const subject = email?.subject || '(no subject)';
    const rescan = !!(email?.calendar_extracted || (email?.calendar_event_count || 0) > 0);
    busyEmailId = emailId;
    if (statusEl) {
      statusEl.hidden = false;
      statusEl.textContent = rescan
        ? 'Re-scanning calendar dates…'
        : 'Extracting calendar events with Ollama…';
    }
    appendActivityLog({
      ts: new Date().toLocaleTimeString(),
      level: 'info',
      message: rescan
        ? `Re-scan requested (spam → middleman → calendar) — ${subject}`
        : `Scan requested (spam → middleman → calendar) — ${subject}`,
    });
    renderResults(currentPayload());

    try {
      const res = await fetch(`/api/search/emails/${encodeURIComponent(emailId)}/calendar`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: true }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Calendar extraction failed');
      const count = data.events_found || 0;
      if (statusEl) {
        let statusText =
          count > 0
            ? `Added ${count} event${count === 1 ? '' : 's'} to calendar.`
            : 'No calendar dates found — not added to calendar.';
        if (data.possible_indian_middleman) {
          statusText += ' Middleman flagged.';
        }
        if (data.is_spam) {
          statusText += ' Spam/junk.';
        }
        statusEl.textContent = statusText;
      }
      if (Array.isArray(data.logs) && data.logs.length) {
        renderActivityLog(data.logs);
      } else {
        appendActivityLog({
          ts: new Date().toLocaleTimeString(),
          level: data.error ? 'warning' : 'info',
          message: data.message || `Re-scan finished — ${subject}`,
        });
      }
      await runSearch(currentQuery);
    } catch (e) {
      if (statusEl) statusEl.textContent = `Error: ${e}`;
      appendActivityLog({
        ts: new Date().toLocaleTimeString(),
        level: 'warning',
        message: `Calendar ${rescan ? 're-scan' : 'extract'} failed — ${subject}: ${e}`,
      });
    } finally {
      busyEmailId = null;
      renderResults(currentPayload());
    }
  }

  function dismissResult(emailId) {
    if (!emailId) return;
    if (selectedEmailId === emailId) closeSourceEmail();
    dismissed.add(String(emailId));
    saveDismissed();
    renderResults(currentPayload());
  }

  searchForm?.addEventListener('submit', (ev) => {
    ev.preventDefault();
    runSearch(searchQuery?.value || '');
  });

  btnClear?.addEventListener('click', () => {
    if (searchQuery) searchQuery.value = '';
    currentQuery = '';
    dismissed = new Set();
    lastResults = [];
    lastTotal = 0;
    if (searchStatus) searchStatus.textContent = '';
    closeSourceEmail();
    renderResults({ results: [] });
    searchQuery?.focus();
  });

  btnClearSearchActivityLog?.addEventListener('click', async () => {
    try {
      await fetch('/api/logs/clear', { method: 'POST' });
      if (searchActivityLog) searchActivityLog.innerHTML = '';
    } catch (_) { /* ignore */ }
  });

  connectActivityStream();

  try {
    const params = new URLSearchParams(window.location.search);
    const q = params.get('q');
    if (q && searchQuery) {
      searchQuery.value = q;
      runSearch(q);
    }
  } catch (_) { /* ignore */ }
})();