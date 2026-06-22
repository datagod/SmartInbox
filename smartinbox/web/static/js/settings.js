(function () {
  const gmailStatus = document.getElementById('gmail-status');
  const btnConnect = document.getElementById('btn-connect');
  const btnDisconnect = document.getElementById('btn-disconnect');
  const pollInterval = document.getElementById('poll-interval');
  const alertCooldown = document.getElementById('alert-cooldown');
  const alertsEnabled = document.getElementById('alerts-enabled');
  const voiceSelect = document.getElementById('voice-select');
  const ttsModel = document.getElementById('tts-model');
  const deliveryMode = document.getElementById('delivery-mode');
  const ollamaStatus = document.getElementById('ollama-status');

  let voiceChoices = [];

  const params = new URLSearchParams(window.location.search);
  if (params.get('oauth') === 'ok') {
    gmailStatus.textContent = 'Gmail connected successfully.';
    history.replaceState({}, '', '/settings');
  } else if (params.get('oauth') === 'denied') {
    gmailStatus.textContent = 'Gmail authorization was denied.';
    history.replaceState({}, '', '/settings');
  } else if (params.get('oauth') === 'error') {
    gmailStatus.textContent = 'Gmail authorization failed — check server log.';
    history.replaceState({}, '', '/settings');
  }

  async function loadSettings() {
    const [settingsRes, healthRes, voicesRes] = await Promise.all([
      fetch('/api/settings'),
      fetch('/api/health'),
      fetch('/api/tts/voices'),
    ]);
    const settings = await settingsRes.json();
    const health = await healthRes.json();
    const voices = await voicesRes.json();

    const gmail = settings.gmail || {};
    if (gmail.connected) {
      gmailStatus.textContent = `Connected as ${gmail.email}`;
      btnConnect.hidden = true;
      btnDisconnect.hidden = false;
    } else {
      gmailStatus.textContent = 'Not connected';
      btnConnect.hidden = false;
      btnDisconnect.hidden = true;
    }

    pollInterval.value = String(settings.poll_interval || 60);
    alertCooldown.value = String(settings.alert_cooldown || 120);
    alertsEnabled.value = settings.alerts_enabled ? '1' : '0';

    const ollama = health.ollama || {};
    if (ollama.reachable && ollama.model_listed) {
      ollamaStatus.textContent = 'Ollama reachable — model loaded.';
    } else if (ollama.reachable) {
      ollamaStatus.textContent = `Ollama reachable — ${ollama.error || 'model not listed'}`;
    } else {
      ollamaStatus.textContent = `Ollama unreachable — ${ollama.error || 'check config'}`;
    }

    if (voices.ok) {
      voiceChoices = [];
      (voices.voices.clone || []).forEach((v) => {
        voiceChoices.push({ mode: 'clone', id: v.id, label: `Clone: ${v.label}` });
      });
      (voices.voices.predefined || []).forEach((v) => {
        voiceChoices.push({ mode: 'predefined', id: v.id, label: `Preset: ${v.label}` });
      });
      voiceSelect.innerHTML = voiceChoices
        .map((v) => `<option value="${v.mode}|${v.id}">${v.label}</option>`)
        .join('');

      ttsModel.innerHTML = (voices.models || [])
        .map((m) => `<option value="${m.id}">${m.label}</option>`)
        .join('');
      ttsModel.value = voices.tts_model || 'chatterbox-turbo';
      deliveryMode.value = voices.delivery_mode || 'normal';

      const chosen = voices.chosen;
      if (chosen) {
        voiceSelect.value = `${chosen.voice_mode}|${chosen.voice}`;
      }
    }
  }

  document.getElementById('btn-save-timing').addEventListener('click', async () => {
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        poll_interval: Number(pollInterval.value),
        alert_cooldown: Number(alertCooldown.value),
        alerts_enabled: alertsEnabled.value === '1',
      }),
    });
    gmailStatus.textContent = 'Timing settings saved.';
  });

  document.getElementById('btn-save-voice').addEventListener('click', async () => {
    const [mode, voice] = voiceSelect.value.split('|');
    await fetch('/api/tts/voice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        voice_mode: mode,
        voice: voice,
        delivery_mode: deliveryMode.value,
        tts_model: ttsModel.value,
      }),
    });
    gmailStatus.textContent = 'Voice settings saved.';
  });

  document.getElementById('btn-test-speak').addEventListener('click', async () => {
    const res = await fetch('/api/tts/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delivery_mode: deliveryMode.value }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      gmailStatus.textContent = err.error || 'TTS test failed';
      return;
    }
    const blob = await res.blob();
    const audio = new Audio(URL.createObjectURL(blob));
    audio.play();
  });

  btnDisconnect.addEventListener('click', async () => {
    await fetch('/api/auth/google/disconnect', { method: 'POST' });
    await loadSettings();
  });

  loadSettings();
})();