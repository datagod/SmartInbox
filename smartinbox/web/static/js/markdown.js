(function (global) {
  const SECTION_TITLES = {
    summary: 'Summary',
    'key points': 'Key points',
    'key point': 'Key points',
    'action needed': 'Action needed',
    'action items': 'Action needed',
    actions: 'Action needed',
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function inlineFormat(s) {
    let out = escapeHtml(s);
    out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return out;
  }

  function normalizeHeading(title) {
    const raw = String(title || '').trim().replace(/:+\s*$/, '');
    const lower = raw.toLowerCase();
    if (lower === 'from' || lower.startsWith('from ')) {
      const sender = raw.replace(/^from\s*/i, '').trim();
      return { kind: 'from', label: sender || raw };
    }
    const section = SECTION_TITLES[lower];
    if (section) {
      return { kind: 'section', label: section, slug: lower.replace(/\s+/g, '-') };
    }
    return null;
  }

  function renderMarkdown(text) {
    const raw = String(text || '').trim();
    if (!raw) {
      return '<p class="summary-empty">(no summary yet)</p>';
    }

    const lines = raw.split('\n');
    const html = [];
    let inList = false;
    let listType = null;

    function closeList() {
      if (!inList) return;
      html.push(listType === 'ol' ? '</ol>' : '</ul>');
      inList = false;
      listType = null;
    }

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) {
        closeList();
        continue;
      }

      const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
      const ul = trimmed.match(/^[-*+]\s+(.+)$/);
      const ol = trimmed.match(/^\d+\.\s+(.+)$/);

      if (heading) {
        closeList();
        const normalized = normalizeHeading(heading[2]);
        if (normalized?.kind === 'from') {
          html.push(`<div class="summary-from">${inlineFormat(normalized.label)}</div>`);
          continue;
        }
        if (normalized?.kind === 'section') {
          html.push(
            `<h2 class="summary-section summary-section-${normalized.slug}">${inlineFormat(normalized.label)}</h2>`
          );
          continue;
        }
        if (heading[1] === '###') {
          html.push(`<h3>${inlineFormat(heading[2].replace(/:+\s*$/, ''))}</h3>`);
          continue;
        }
        html.push(`<h2>${inlineFormat(heading[2].replace(/:+\s*$/, ''))}</h2>`);
        continue;
      }

      const boldSection = trimmed.match(/^\*\*([^*]+)\*\*\s*$/);
      if (boldSection) {
        const normalized = normalizeHeading(boldSection[1]);
        if (normalized?.kind === 'section') {
          closeList();
          html.push(
            `<h2 class="summary-section summary-section-${normalized.slug}">${inlineFormat(normalized.label)}</h2>`
          );
          continue;
        }
      }

      if (ul) {
        if (!inList || listType !== 'ul') {
          closeList();
          html.push('<ul>');
          inList = true;
          listType = 'ul';
        }
        html.push(`<li>${inlineFormat(ul[1])}</li>`);
        continue;
      }
      if (ol) {
        if (!inList || listType !== 'ol') {
          closeList();
          html.push('<ol>');
          inList = true;
          listType = 'ol';
        }
        html.push(`<li>${inlineFormat(ol[1])}</li>`);
        continue;
      }

      closeList();
      html.push(`<p>${inlineFormat(trimmed)}</p>`);
    }

    closeList();
    return html.join('\n');
  }

  global.renderMarkdown = renderMarkdown;
})(window);