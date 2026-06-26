(function () {
  const layout = document.querySelector('.set-layout');
  if (!layout) return;

  const shell = layout.querySelector('.set-activity-shell');
  const panel =
    shell?.querySelector('.set-activity-panel') ||
    layout.querySelector('.set-activity-panel');
  const sizeTarget = shell || panel;
  if (!sizeTarget || !panel) return;

  const fullMode = sizeTarget.classList.contains('set-activity-shell--full');
  const widthResizer =
    sizeTarget.querySelector('.set-activity-resizer--width') ||
    layout.querySelector('.set-activity-resizer');
  const heightResizer =
    sizeTarget.querySelector('.set-activity-resizer--height') ||
    panel.querySelector('.set-activity-height-resizer');
  const cornerResizer = sizeTarget.querySelector('.set-activity-resizer--corner');

  const scope = sizeTarget.dataset.activityScope || (fullMode ? 'calendar' : 'default');
  const WIDTH_KEY = `smartinbox-activity-log-width-${scope}`;
  const HEIGHT_KEY = `smartinbox-activity-log-height-${scope}`;
  const MIN_W = 200;
  const MIN_H = 160;
  const DEFAULT_W = fullMode ? 480 : 448;
  const DEFAULT_H = fullMode ? 520 : 420;

  function minMainWidth() {
    return fullMode ? 280 : 360;
  }

  function maxWidth() {
    if (fullMode) {
      return Math.max(MIN_W, layout.clientWidth - minMainWidth());
    }
    return 640;
  }

  function maxHeight() {
    return Math.max(MIN_H, window.innerHeight - (fullMode ? 24 : 48));
  }

  function clampWidth(px) {
    return Math.min(maxWidth(), Math.max(MIN_W, px));
  }

  function clampHeight(px) {
    return Math.min(maxHeight(), Math.max(MIN_H, px));
  }

  function applyWidth(px) {
    const w = clampWidth(px);
    sizeTarget.style.setProperty('--activity-log-width', `${w}px`);
    sizeTarget.style.width = `${w}px`;
    return w;
  }

  function applyHeight(px) {
    const h = clampHeight(px);
    sizeTarget.style.setProperty('--activity-log-height', `${h}px`);
    sizeTarget.style.height = `${h}px`;
    sizeTarget.classList.add('has-custom-height');
    panel.classList.add('has-custom-height');
    return h;
  }

  function clearSizes() {
    sizeTarget.style.removeProperty('--activity-log-width');
    sizeTarget.style.removeProperty('--activity-log-height');
    sizeTarget.style.removeProperty('width');
    sizeTarget.style.removeProperty('height');
    sizeTarget.classList.remove('has-custom-height');
    panel.classList.remove('has-custom-height');
    localStorage.removeItem(WIDTH_KEY);
    localStorage.removeItem(HEIGHT_KEY);
    if (fullMode) applyHeight(DEFAULT_H);
    applyWidth(DEFAULT_W);
  }

  function readInt(key) {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : null;
  }

  const savedW = readInt(WIDTH_KEY);
  const savedH = readInt(HEIGHT_KEY);
  if (savedW != null) applyWidth(savedW);
  else applyWidth(DEFAULT_W);
  if (savedH != null) applyHeight(savedH);
  else if (fullMode) applyHeight(DEFAULT_H);

  function persistSizes() {
    localStorage.setItem(WIDTH_KEY, String(sizeTarget.offsetWidth));
    if (sizeTarget.classList.contains('has-custom-height')) {
      localStorage.setItem(HEIGHT_KEY, String(sizeTarget.offsetHeight));
    }
  }

  function bindWidthResize(handle) {
    if (!handle) return;

    handle.addEventListener('dblclick', () => {
      applyWidth(DEFAULT_W);
      localStorage.setItem(WIDTH_KEY, String(DEFAULT_W));
    });

    handle.addEventListener('keydown', (e) => {
      let next = sizeTarget.offsetWidth;
      if (e.key === 'ArrowLeft') next += 16;
      if (e.key === 'ArrowRight') next -= 16;
      if (e.key === 'Home') next = maxWidth();
      if (e.key === 'End') next = MIN_W;
      if (next === sizeTarget.offsetWidth) return;
      e.preventDefault();
      const w = applyWidth(next);
      localStorage.setItem(WIDTH_KEY, String(w));
    });

    handle.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const startX = e.clientX;
      const startW = sizeTarget.offsetWidth;
      handle.classList.add('is-dragging');
      document.body.classList.add('is-resizing-activity-log-width');

      function onMove(ev) {
        applyWidth(startW + (startX - ev.clientX));
      }

      function onUp() {
        handle.classList.remove('is-dragging');
        document.body.classList.remove('is-resizing-activity-log-width');
        persistSizes();
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  function bindHeightResize(handle) {
    if (!handle) return;

    handle.addEventListener('dblclick', () => {
      applyHeight(DEFAULT_H);
      localStorage.setItem(HEIGHT_KEY, String(DEFAULT_H));
    });

    handle.addEventListener('keydown', (e) => {
      let next = sizeTarget.offsetHeight;
      if (e.key === 'ArrowDown') next += 16;
      if (e.key === 'ArrowUp') next -= 16;
      if (e.key === 'Home') next = maxHeight();
      if (e.key === 'End') next = MIN_H;
      if (next === sizeTarget.offsetHeight) return;
      e.preventDefault();
      const h = applyHeight(next);
      localStorage.setItem(HEIGHT_KEY, String(h));
    });

    handle.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const startY = e.clientY;
      const startH = sizeTarget.offsetHeight;
      handle.classList.add('is-dragging');
      document.body.classList.add('is-resizing-activity-log-height');

      function onMove(ev) {
        applyHeight(startH + (ev.clientY - startY));
      }

      function onUp() {
        handle.classList.remove('is-dragging');
        document.body.classList.remove('is-resizing-activity-log-height');
        persistSizes();
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  function bindCornerResize(handle) {
    if (!handle) return;

    handle.addEventListener('dblclick', () => {
      clearSizes();
      localStorage.setItem(WIDTH_KEY, String(DEFAULT_W));
      localStorage.setItem(HEIGHT_KEY, String(DEFAULT_H));
    });

    handle.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const startX = e.clientX;
      const startY = e.clientY;
      const startW = sizeTarget.offsetWidth;
      const startH = sizeTarget.offsetHeight;
      handle.classList.add('is-dragging');
      document.body.classList.add('is-resizing-activity-log-corner');

      function onMove(ev) {
        applyWidth(startW + (startX - ev.clientX));
        applyHeight(startH + (ev.clientY - startY));
      }

      function onUp() {
        handle.classList.remove('is-dragging');
        document.body.classList.remove('is-resizing-activity-log-corner');
        persistSizes();
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  bindWidthResize(widthResizer);
  bindHeightResize(heightResizer);
  bindCornerResize(cornerResizer);

  window.addEventListener('resize', () => {
    if (!sizeTarget.classList.contains('has-custom-height')) return;
    const raw = sizeTarget.style.getPropertyValue('--activity-log-height');
    const n = parseInt(raw, 10);
    if (Number.isFinite(n)) applyHeight(n);
    const wRaw = sizeTarget.style.getPropertyValue('--activity-log-width');
    const w = parseInt(wRaw, 10);
    if (Number.isFinite(w)) applyWidth(w);
  });
})();