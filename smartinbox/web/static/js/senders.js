(function () {
  const upvotedTbody = document.getElementById('upvoted-tbody');
  const downvotedTbody = document.getElementById('downvoted-tbody');
  const upvotedStatus = document.getElementById('upvoted-status');
  const downvotedStatus = document.getElementById('downvoted-status');
  const btnRefresh = document.getElementById('btn-refresh-senders');

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatScore(score) {
    const n = Number(score) || 0;
    if (n > 0) return `+${n}`;
    return String(n);
  }

  function renderRows(tbody, rows, emptyMessage, voteClass) {
    if (!tbody) return 0;
    if (!rows || !rows.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="senders-empty">${emptyMessage}</td></tr>`;
      return 0;
    }
    tbody.innerHTML = rows
      .map((row, index) => {
        const display = escapeHtml(row.display || row.sender_key || 'unknown');
        const key = escapeHtml(row.sender_key || '');
        const up = Number(row.upvotes) || 0;
        const down = Number(row.downvotes) || 0;
        const score = formatScore(row.score);
        const scoreClass = row.score > 0 ? 'score-up' : row.score < 0 ? 'score-down' : '';
        const voteHighlight = voteClass === 'up' ? 'vote-count-up' : 'vote-count-down';
        return `<tr>
          <td class="col-rank">${index + 1}</td>
          <td class="col-sender"><span class="sender-display">${display}</span><span class="sender-key">${key}</span></td>
          <td class="col-votes${voteClass === 'up' ? ' vote-count-up' : ''}">${up}</td>
          <td class="col-votes${voteClass === 'down' ? ' vote-count-down' : ''}">${down}</td>
          <td class="col-score ${scoreClass}">${score}</td>
        </tr>`;
      })
      .join('');
    return rows.length;
  }

  async function loadSenders() {
    if (upvotedStatus) upvotedStatus.textContent = 'Loading…';
    if (downvotedStatus) downvotedStatus.textContent = 'Loading…';
    try {
      const res = await fetch('/api/senders');
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Failed to load senders');
      const upCount = renderRows(
        upvotedTbody,
        data.upvoted,
        'No upvoted senders yet. Use ▲ on an email in the Inbox.',
        'up'
      );
      const downCount = renderRows(
        downvotedTbody,
        data.downvoted,
        'No downvoted senders yet. Use ▼ on an email in the Inbox.',
        'down'
      );
      if (upvotedStatus) {
        upvotedStatus.textContent = upCount
          ? `${upCount} sender${upCount === 1 ? '' : 's'} with upvotes`
          : '';
      }
      if (downvotedStatus) {
        downvotedStatus.textContent = downCount
          ? `${downCount} sender${downCount === 1 ? '' : 's'} with downvotes`
          : '';
      }
    } catch (e) {
      if (upvotedStatus) upvotedStatus.textContent = `Error: ${e}`;
      if (downvotedStatus) downvotedStatus.textContent = '';
      if (upvotedTbody) {
        upvotedTbody.innerHTML =
          '<tr><td colspan="5" class="senders-empty">Could not load sender rankings.</td></tr>';
      }
      if (downvotedTbody) {
        downvotedTbody.innerHTML =
          '<tr><td colspan="5" class="senders-empty">Could not load sender rankings.</td></tr>';
      }
    }
  }

  btnRefresh?.addEventListener('click', () => loadSenders());
  loadSenders();
})();