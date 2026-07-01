(function () {
  const storageStatus = document.getElementById('storage-status');
  const storageOverview = document.getElementById('storage-overview');
  const storageSourceTbody = document.getElementById('storage-source-tbody');
  const storageDates = document.getElementById('storage-dates');
  const storageAgeBuckets = document.getElementById('storage-age-buckets');
  const storageSpace = document.getElementById('storage-space');
  const storageProcessing = document.getElementById('storage-processing');
  const storageMonthTbody = document.getElementById('storage-month-tbody');
  const storageRecentTbody = document.getElementById('storage-recent-tbody');
  const storageDbPath = document.getElementById('storage-db-path');
  const btnRefresh = document.getElementById('btn-refresh-storage');

  const PROVIDER_LABELS = { gmail: 'Gmail', proton: 'Proton', unknown: 'Unknown' };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function providerLabel(key) {
    const k = String(key || 'unknown').toLowerCase();
    return PROVIDER_LABELS[k] || k.charAt(0).toUpperCase() + k.slice(1);
  }

  function formatBytes(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  function formatTs(ts) {
    const n = Number(ts);
    if (!n) return '—';
    return new Date(n * 1000).toLocaleString(undefined, {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  function formatMonth(ym) {
    if (!ym) return '—';
    const [y, m] = String(ym).split('-').map(Number);
    if (!y || !m) return ym;
    return new Date(y, m - 1, 1).toLocaleString(undefined, { month: 'long', year: 'numeric' });
  }

  function dlItem(label, value) {
    return `<div class="storage-dl-row"><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`;
  }

  function renderOverview(stats, connected) {
    if (!storageOverview) return;
    const total = stats.total_emails || 0;
    const db = stats.database || {};
    const accounts = (connected || []).filter((a) => a.connected).length;
    storageOverview.innerHTML = `
      <div class="storage-stat-card">
        <span class="storage-stat-value">${total.toLocaleString()}</span>
        <span class="storage-stat-label">Stored emails</span>
      </div>
      <div class="storage-stat-card">
        <span class="storage-stat-value">${accounts}</span>
        <span class="storage-stat-label">Connected accounts</span>
      </div>
      <div class="storage-stat-card">
        <span class="storage-stat-value">${formatBytes(db.total_bytes)}</span>
        <span class="storage-stat-label">Database on disk</span>
      </div>
      <div class="storage-stat-card">
        <span class="storage-stat-value">${formatBytes(stats.content?.content_bytes)}</span>
        <span class="storage-stat-label">Email text content</span>
      </div>`;
  }

  function renderBySource(stats) {
    if (!storageSourceTbody) return;
    const rows = stats.by_account || [];
    const total = stats.total_emails || 0;
    if (!rows.length) {
      storageSourceTbody.innerHTML =
        '<tr><td colspan="4" class="storage-empty">No stored emails yet. Use Pipelines to fetch mail.</td></tr>';
      return;
    }
    storageSourceTbody.innerHTML = rows
      .map((row) => {
        const count = Number(row.count) || 0;
        const pct = total ? Math.round((count / total) * 100) : 0;
        return `<tr>
          <td>${escapeHtml(providerLabel(row.provider))}</td>
          <td>${escapeHtml(row.account_email || '—')}</td>
          <td class="col-num">${count.toLocaleString()}</td>
          <td class="col-num">${pct}%</td>
        </tr>`;
      })
      .join('');
  }

  function renderDates(stats) {
    if (!storageDates) return;
    const d = stats.dates || {};
    storageDates.innerHTML = [
      dlItem('Oldest received', formatTs(d.oldest_received)),
      dlItem('Newest received', formatTs(d.newest_received)),
      dlItem('First stored locally', formatTs(d.oldest_stored)),
      dlItem('Last stored locally', formatTs(d.newest_stored)),
    ].join('');
  }

  function renderAgeBuckets(stats) {
    if (!storageAgeBuckets) return;
    const buckets = stats.age_buckets || [];
    if (!buckets.length) {
      storageAgeBuckets.innerHTML = dlItem('Emails', '0');
      return;
    }
    storageAgeBuckets.innerHTML = buckets
      .map((b) => dlItem(b.label, (Number(b.count) || 0).toLocaleString()))
      .join('');
  }

  function renderSpace(stats) {
    if (!storageSpace) return;
    const c = stats.content || {};
    const db = stats.database || {};
    storageSpace.innerHTML = [
      dlItem('Database file', `${formatBytes(db.file_bytes)} <span class="storage-muted">(${escapeHtml(db.path || '')})</span>`),
      dlItem('WAL journal', formatBytes(db.wal_bytes)),
      dlItem('Shared memory', formatBytes(db.shm_bytes)),
      dlItem('Total on disk', formatBytes(db.total_bytes)),
      dlItem('Body text', formatBytes(c.body_bytes)),
      dlItem('Snippets', formatBytes(c.snippet_bytes)),
      dlItem('Summaries', formatBytes(c.summary_bytes)),
      dlItem('Subjects & senders', formatBytes(c.meta_bytes)),
      dlItem('Avg body per email', formatBytes(c.avg_body_bytes)),
    ].join('');
  }

  function renderProcessing(stats) {
    if (!storageProcessing) return;
    storageProcessing.innerHTML = [
      dlItem('Summarized', (stats.summarized || 0).toLocaleString()),
      dlItem('Awaiting summary', (stats.unsummarized || 0).toLocaleString()),
      dlItem('Alerted', (stats.alerted || 0).toLocaleString()),
      dlItem('Starred', (stats.starred || 0).toLocaleString()),
      dlItem('Calendar events', (stats.calendar_events || 0).toLocaleString()),
      dlItem('Emails scanned for dates', (stats.calendar_scanned || 0).toLocaleString()),
    ].join('');
  }

  function renderByMonth(stats) {
    if (!storageMonthTbody) return;
    const rows = stats.by_month || [];
    if (!rows.length) {
      storageMonthTbody.innerHTML =
        '<tr><td colspan="3" class="storage-empty">No monthly data yet.</td></tr>';
      return;
    }
    const max = Math.max(...rows.map((r) => Number(r.count) || 0), 1);
    storageMonthTbody.innerHTML = rows
      .map((row) => {
        const count = Number(row.count) || 0;
        const width = Math.max(4, Math.round((count / max) * 100));
        return `<tr>
          <td>${escapeHtml(formatMonth(row.month))}</td>
          <td class="col-num">${count.toLocaleString()}</td>
          <td class="col-bar"><span class="storage-bar" style="width:${width}%"></span></td>
        </tr>`;
      })
      .join('');
  }

  function renderRecent(stats) {
    if (!storageRecentTbody) return;
    const rows = stats.recent || [];
    if (!rows.length) {
      storageRecentTbody.innerHTML =
        '<tr><td colspan="4" class="storage-empty">No emails stored yet.</td></tr>';
      return;
    }
    storageRecentTbody.innerHTML = rows
      .map((row) => {
        const src = providerLabel(row.provider);
        const acct = row.account_email ? ` · ${row.account_email}` : '';
        return `<tr>
          <td class="storage-ts">${escapeHtml(formatTs(row.sort_ts))}</td>
          <td>${escapeHtml(src + acct)}</td>
          <td class="storage-subject" title="${escapeHtml(row.subject)}">${escapeHtml(row.subject)}</td>
          <td class="col-num">${formatBytes(row.approx_bytes)}</td>
        </tr>`;
      })
      .join('');
  }

  async function loadStorage() {
    if (storageStatus) storageStatus.textContent = 'Loading…';
    try {
      const res = await fetch('/api/storage');
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Failed to load storage stats');
      const stats = data.stats || {};
      renderOverview(stats, data.connected_accounts);
      renderBySource(stats);
      renderDates(stats);
      renderAgeBuckets(stats);
      renderSpace(stats);
      renderProcessing(stats);
      renderByMonth(stats);
      renderRecent(stats);
      if (storageDbPath) {
        storageDbPath.textContent = stats.database?.path
          ? `Database: ${stats.database.path}`
          : '';
      }
      const parts = [];
      if (data.demo_mode) {
        parts.push('Demo mode — inbox shows sample emails; stats below are from your real database');
      }
      parts.push(`Updated ${new Date().toLocaleTimeString()}`);
      if (storageStatus) storageStatus.textContent = parts.join(' · ');
    } catch (e) {
      if (storageStatus) storageStatus.textContent = `Error: ${e}`;
    }
  }

  btnRefresh?.addEventListener('click', () => loadStorage());
  loadStorage();
})();