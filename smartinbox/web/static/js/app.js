(function () {
  const inboxList = document.getElementById('inbox-list');
  const inboxCount = document.getElementById('inbox-count');
  const summaryViewport = document.getElementById('summary-viewport');
  const summaryBody = document.getElementById('summary-body');
  const summaryTheme = document.getElementById('summary-theme');
  const activityLog = document.getElementById('activity-log');
  const connStatus = document.getElementById('conn-status');
  const btnPoll = document.getElementById('btn-poll');
  const btnEmptyInbox = document.getElementById('btn-empty-inbox');
  const btnClearActivityLog = document.getElementById('btn-clear-activity-log');
  const btnResummarize = document.getElementById('btn-resummarize');
  const summaryViewOptions = document.querySelectorAll('.summary-view-option');

  const THEME_KEY = 'smartinbox.summaryTheme';
  const THEMES = [
    'modern',
    'ansi',
    'arcade',
    'phosphor',
    'amber',
    'typewriter',
    'teleprinter',
    'dotmatrix',
    'newsprint',
    'solarized',
    'blueprint',
    'mainframe',
    'pdp11',
    'mailcraft',
    'pacmail',
    'lsmail',
    'c64',
    'macintosh',
  ];

  let emails = [];
  let demoMode = false;
  let selectedId = null;
  let summaryViewMode = 'summary';
  let importantKeys = new Set();
  let senderInterest = {};
  const audioQueue = [];
  let playing = false;

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

  function buildLogElement(entry) {
    const div = document.createElement('div');
    const lvl = (entry.level || 'info').replace('warning', 'warn');
    const isInboxCheck = String(entry.message || '').startsWith('Inbox check —');
    div.className = isInboxCheck ? 'activity-entry activity-entry--success' : 'activity-entry';
    const tsClass = isInboxCheck ? 'success' : lvl;
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

  function isStarred(email) {
    return Boolean(email && (email.starred === 1 || email.starred === true));
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

  function sortEmailsDesc(list) {
    return [...(list || [])].sort((a, b) => emailSortKey(b) - emailSortKey(a));
  }

  function renderInbox() {
    if (!emails.length) {
      inboxList.innerHTML = demoMode
        ? '<p class="summary-empty">Demo inbox is empty. Turn demo mode off and on in Settings to restore samples.</p>'
        : '<p class="summary-empty">No emails yet. Connect Gmail or Proton in Settings.</p>';
      inboxCount.textContent = demoMode ? 'DEMO · 0 sample emails' : '0 emails';
      return;
    }
    const sorted = sortEmailsDesc(emails);
    const countLabel = sorted.length === 1 ? 'email' : 'emails';
    inboxCount.textContent = demoMode
      ? `DEMO · ${sorted.length} sample ${countLabel}`
      : `${sorted.length} ${countLabel}`;
    inboxList.innerHTML = sorted
      .map((e) => {
        const sel = e.id === selectedId ? ' selected' : '';
        const starred = isStarred(e);
        const score = senderScore(e.sender);
        const junk = score < 0 ? ' junk' : '';
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
        return `<div class="email-item${sel}${imp}${starred ? ' starred' : ''}${junk}" data-id="${escapeHtml(e.id)}">
          <div class="email-votes" role="group" aria-label="Rate sender">
            <button type="button" class="vote-btn vote-up${upActive}" data-vote="up" title="Interested in this sender" aria-label="Upvote sender">▲</button>
            <button type="button" class="vote-btn vote-down${downActive}" data-vote="down" title="Mark sender as junk" aria-label="Downvote sender">▼</button>
          </div>
          <div class="email-item-body">
            <div class="email-subject">${badge}${prov}${escapeHtml(e.subject || '(no subject)')}</div>
            <div class="email-meta">${escapeHtml(e.sender || '')} · ${fmtTime(e.received_at)}</div>
          </div>
        </div>`;
      })
      .join('');
    inboxList.querySelectorAll('.email-item').forEach((el) => {
      el.addEventListener('click', (ev) => {
        if (ev.target.closest('.vote-btn')) return;
        selectEmail(el.dataset.id);
      });
      el.addEventListener('dblclick', (ev) => {
        if (ev.target.closest('.vote-btn')) return;
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
    });
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
    summaryBody.className = 'summary-body markdown-body';
    summaryBody.innerHTML = renderMarkdown(text);
  }

  function showSummaryMessage(text, kind) {
    summaryViewMode = 'summary';
    updateViewSwitcher();
    summaryBody.className = `summary-body${kind ? ` summary-${kind}` : ''}`;
    summaryBody.textContent = text;
  }

  function updateViewSwitcher() {
    if (!summaryViewOptions.length) return;
    const hasSelection = !!selectedId;
    summaryViewOptions.forEach((btn) => {
      const view = btn.dataset.view;
      const active = hasSelection && summaryViewMode === view;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      btn.disabled = !hasSelection;
    });
  }

  function showOriginalEmail(row) {
    const from = row.sender || '(unknown)';
    const subject = row.subject || '(no subject)';
    const date = fmtTime(row.received_at);
    const snippetOnly = !row.body_text && !!row.snippet;
    const bodyHtml = formatOriginalEmailBody(row.body_text || row.snippet || '');
    summaryBody.className = 'summary-body original-email-view';
    summaryBody.innerHTML =
      '<div class="original-email-meta">' +
      `<p><span class="original-label">From</span> ${escapeHtml(from)}</p>` +
      `<p><span class="original-label">Subject</span> ${escapeHtml(subject)}</p>` +
      `<p><span class="original-label">Date</span> ${escapeHtml(date)}</p>` +
      '</div>' +
      (snippetOnly
        ? '<p class="original-email-note">Showing snippet only — full message body is not stored.</p>'
        : '') +
      `<div class="original-email-body">${bodyHtml}</div>`;
  }

  function renderSummaryPanel() {
    if (!selectedId) return;
    const row = emails.find((e) => e.id === selectedId);
    if (!row) return;
    if (summaryViewMode === 'original') {
      showOriginalEmail(row);
      return;
    }
    const text = row.summary_detailed || row.summary_short || row.snippet || '';
    if (text) {
      showSummaryMarkdown(text);
    } else {
      summaryBody.className = 'summary-body markdown-body';
      summaryBody.innerHTML = '<p class="summary-empty">(no summary yet)</p>';
    }
  }

  function selectEmail(id) {
    selectedId = id;
    summaryViewMode = 'summary';
    renderInbox();
    renderSummaryPanel();
    btnResummarize.disabled = false;
    updateViewSwitcher();
  }

  function enqueueAlert(alert) {
    if (!alert || !alert.recording) return;
    audioQueue.push(`/api/recordings/${encodeURIComponent(alert.recording)}`);
    drainQueue();
  }

  function drainQueue() {
    if (playing || !audioQueue.length) return;
    playing = true;
    const url = audioQueue.shift();
    const audio = new Audio(url);
    audio.onended = () => {
      playing = false;
      drainQueue();
    };
    audio.onerror = () => {
      playing = false;
      drainQueue();
    };
    audio.play().catch(() => {
      playing = false;
      drainQueue();
    });
  }

  function applySnapshot(snap) {
    demoMode = !!snap.demo_mode;
    emails = sortEmailsDesc(snap.emails || []);
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
          emails = sortEmailsDesc(msg.data || []);
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
        if (msg.type === 'email_alerts') {
          (msg.data || []).forEach(enqueueAlert);
        }
      } catch (_) { /* ignore */ }
    };
    es.onerror = () => {
      connStatus.textContent = 'SSE disconnected — retrying…';
      connStatus.className = 'status-line error';
    };
  }

  btnPoll.addEventListener('click', async () => {
    btnPoll.disabled = true;
    try {
      await fetch('/api/poll', { method: 'POST' });
    } finally {
      btnPoll.disabled = false;
    }
  });

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
    btnEmptyInbox.addEventListener('click', async () => {
      if (!emails.length) return;
      if (!window.confirm('Remove all emails from the inbox?')) return;
      btnEmptyInbox.disabled = true;
      try {
        const res = await fetch('/api/inbox/empty', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          appendLog({
            ts: new Date().toLocaleTimeString(),
            level: 'error',
            message: data.error || 'Failed to empty inbox',
          });
        }
      } catch (e) {
        appendLog({
          ts: new Date().toLocaleTimeString(),
          level: 'error',
          message: `Failed to empty inbox: ${e}`,
        });
      } finally {
        btnEmptyInbox.disabled = false;
      }
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
    let savedTheme = 'modern';
    try {
      savedTheme = localStorage.getItem(THEME_KEY) || 'modern';
    } catch (_) { /* ignore */ }
    applySummaryTheme(savedTheme);
    summaryTheme.addEventListener('change', () => {
      applySummaryTheme(summaryTheme.value);
    });
  }

  connectSSE();
})();