(function () {
  const VOLUME_KEY = 'smartinbox-alert-volume';

  function clampVolume(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return 1;
    return Math.max(0, Math.min(1, n));
  }

  function loadAlertVolume() {
    try {
      const raw = localStorage.getItem(VOLUME_KEY);
      if (raw == null) return 1;
      return clampVolume(Number(raw));
    } catch (_) {
      return 1;
    }
  }

  const audioQueue = [];
  let playing = false;
  let currentAlertAudio = null;

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
    audio.volume = loadAlertVolume();
    currentAlertAudio = audio;
    audio.onended = () => {
      currentAlertAudio = null;
      playing = false;
      drainQueue();
    };
    audio.onerror = () => {
      currentAlertAudio = null;
      playing = false;
      drainQueue();
    };
    audio.play().catch(() => {
      currentAlertAudio = null;
      playing = false;
      drainQueue();
    });
  }

  window.smartinboxEnqueueAlert = enqueueAlert;
  window.smartinboxSetAlertVolume = function (volume) {
    if (currentAlertAudio) currentAlertAudio.volume = clampVolume(volume);
  };

  const es = new EventSource('/api/stream');
  es.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'email_alerts') {
        (msg.data || []).forEach(enqueueAlert);
      }
    } catch (_) { /* ignore */ }
  };
})();