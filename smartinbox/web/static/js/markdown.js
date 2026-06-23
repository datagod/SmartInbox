(function (global) {
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

      const h3 = trimmed.match(/^###\s+(.+)$/);
      const h2 = trimmed.match(/^##\s+(.+)$/);
      const h1 = trimmed.match(/^#\s+(.+)$/);
      const ul = trimmed.match(/^[-*+]\s+(.+)$/);
      const ol = trimmed.match(/^\d+\.\s+(.+)$/);

      if (h1 || h2) {
        closeList();
        const title = (h2 || h1)[1];
        html.push(`<h2>${inlineFormat(title)}</h2>`);
        continue;
      }
      if (h3) {
        closeList();
        html.push(`<h3>${inlineFormat(h3[1])}</h3>`);
        continue;
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