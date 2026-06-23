(function () {
  const inboxList = document.getElementById('inbox-list');
  const inboxCount = document.getElementById('inbox-count');
  const summaryViewport = document.getElementById('summary-viewport');
  const summaryBody = document.getElementById('summary-body');
  const summaryTheme = document.getElementById('summary-theme');
  const activityLog = document.getElementById('activity-log');
  const connStatus = document.getElementById('conn-status');
  const btnPoll = document.getElementById('btn-poll');
  const btnResummarize = document.getElementById('btn-resummarize');

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
  ];

  let emails = [];
  let selectedId = null;
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
    div.className = 'activity-entry';
    const lvl = (entry.level || 'info').replace('warning', 'warn');
    div.innerHTML = `<span class="lvl-${lvl}">[${entry.ts}]</span> ${escapeHtml(entry.message)}`;
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
      inboxList.innerHTML = '<p class="summary-empty">No emails yet. Connect Gmail or Proton in Settings.</p>';
      inboxCount.textContent = '0 emails';
      return;
    }
    const sorted = sortEmailsDesc(emails);
    inboxCount.textContent = `${sorted.length} emails`;
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
    summaryBody.className = `summary-body${kind ? ` summary-${kind}` : ''}`;
    summaryBody.textContent = text;
  }

  function selectEmail(id) {
    selectedId = id;
    renderInbox();
    const row = emails.find((e) => e.id === id);
    if (!row) return;
    const text = row.summary_detailed || row.summary_short || row.snippet || '';
    if (text) {
      showSummaryMarkdown(text);
    } else {
      summaryBody.className = 'summary-body markdown-body';
      summaryBody.innerHTML = '<p class="summary-empty">(no summary yet)</p>';
    }
    btnResummarize.disabled = false;
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
    emails = sortEmailsDesc(snap.emails || []);
    importantKeys = new Set(snap.important_sender_keys || []);
    senderInterest = snap.sender_interest || {};
    renderInbox();
    if (selectedId) selectEmail(selectedId);
    renderActivityLog(snap.logs || []);
    const mail = snap.mail_accounts || {};
    const accounts = mail.accounts || [];
    if (accounts.length) {
      const names = accounts
        .map((a) => `${providerLabel(a.provider)}: ${a.email}`)
        .join(' · ');
      connStatus.textContent =
        `${names} · poll ${snap.poll_interval}s · cooldown ${snap.alert_cooldown}s · important: ${snap.important_alert_mode || 'always'}`;
      connStatus.className = 'status-line';
    } else {
      connStatus.textContent = 'No mail accounts connected — open Settings';
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
        if (msg.type === 'emails') {
          emails = sortEmailsDesc(msg.data || []);
          renderInbox();
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