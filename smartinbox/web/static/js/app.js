(function () {
  const inboxList = document.getElementById('inbox-list');
  const inboxCount = document.getElementById('inbox-count');
  const summaryBody = document.getElementById('summary-body');
  const activityLog = document.getElementById('activity-log');
  const connStatus = document.getElementById('conn-status');
  const btnPoll = document.getElementById('btn-poll');
  const btnResummarize = document.getElementById('btn-resummarize');

  let emails = [];
  let selectedId = null;
  const audioQueue = [];
  let playing = false;

  function fmtTime(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString();
  }

  function appendLog(entry) {
    const div = document.createElement('div');
    div.className = 'activity-entry';
    const lvl = (entry.level || 'info').replace('warning', 'warn');
    div.innerHTML = `<span class="lvl-${lvl}">[${entry.ts}]</span> ${escapeHtml(entry.message)}`;
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

  function renderInbox() {
    if (!emails.length) {
      inboxList.innerHTML = '<p class="summary-empty">No emails yet. Connect Gmail in Settings.</p>';
      inboxCount.textContent = '0 emails';
      return;
    }
    inboxCount.textContent = `${emails.length} emails`;
    inboxList.innerHTML = emails
      .map((e) => {
        const sel = e.id === selectedId ? ' selected' : '';
        return `<div class="email-item${sel}" data-id="${escapeHtml(e.id)}">
          <div class="email-subject">${escapeHtml(e.subject || '(no subject)')}</div>
          <div class="email-meta">${escapeHtml(e.sender || '')} · ${fmtTime(e.received_at)}</div>
        </div>`;
      })
      .join('');
    inboxList.querySelectorAll('.email-item').forEach((el) => {
      el.addEventListener('click', () => selectEmail(el.dataset.id));
    });
  }

  function selectEmail(id) {
    selectedId = id;
    renderInbox();
    const row = emails.find((e) => e.id === id);
    if (!row) return;
    const text = row.summary_detailed || row.summary_short || row.snippet || '(no summary yet)';
    summaryBody.textContent = text;
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
    emails = snap.emails || [];
    renderInbox();
    if (selectedId) selectEmail(selectedId);
    (snap.logs || []).slice().reverse().forEach(appendLog);
    const gmail = snap.gmail || {};
    if (gmail.connected) {
      connStatus.textContent = `Gmail: ${gmail.email} · poll every ${snap.poll_interval}s · cooldown ${snap.alert_cooldown}s`;
      connStatus.className = 'status-line';
    } else {
      connStatus.textContent = 'Gmail not connected — open Settings';
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
          emails = msg.data || [];
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
    summaryBody.textContent = 'Summarizing…';
    try {
      const res = await fetch(`/api/summarize/${encodeURIComponent(selectedId)}`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        summaryBody.textContent = data.summary;
        const row = emails.find((e) => e.id === selectedId);
        if (row) {
          row.summary_detailed = data.summary;
          row.summary_short = data.summary.slice(0, 500);
        }
      } else {
        summaryBody.textContent = data.error || 'Summary failed';
      }
    } finally {
      btnResummarize.disabled = false;
    }
  });

  connectSSE();
})();