(function () {
  const gmailStatus = document.getElementById('gmail-status');
  const gmailEmail = document.getElementById('gmail-email');
  const gmailAppPassword = document.getElementById('gmail-app-password');
  const btnConnect = document.getElementById('btn-connect');
  const btnDisconnect = document.getElementById('btn-disconnect');
  const pollInterval = document.getElementById('poll-interval');
  const alertCooldown = document.getElementById('alert-cooldown');
  const alertsEnabled = document.getElementById('alerts-enabled');
  const voiceSelect = document.getElementById('voice-select');
  const ttsModel = document.getElementById('tts-model');
  const deliveryMode = document.getElementById('delivery-mode');
  const alertGreetingName = document.getElementById('alert-greeting-name');
  const alertGreetingEnabled = document.getElementById('alert-greeting-enabled');
  const importantAlertMode = document.getElementById('important-alert-mode');
  const otherAlertMode = document.getElementById('other-alert-mode');
  const importantList = document.getElementById('important-list');
  const protonStatus = document.getElementById('proton-status');
  const protonEmail = document.getElementById('proton-email');
  const protonPassword = document.getElementById('proton-password');
  const btnProtonConnect = document.getElementById('btn-proton-connect');
  const btnProtonDisconnect = document.getElementById('btn-proton-disconnect');

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function renderImportantList(senders) {
    if (!importantList) return;
    if (!senders || !senders.length) {
      importantList.innerHTML = '<li class="important-empty">No important senders yet.</li>';
      return;
    }
    importantList.innerHTML = senders
      .map(
        (s) =>
          `<li class="important-item"><span>${escapeHtml(s.display)}</span>` +
          `<button type="button" class="btn btn-secondary btn-small" data-key="${escapeHtml(s.sender_key)}">Remove</button></li>`
      )
      .join('');
    importantList.querySelectorAll('button[data-key]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await fetch(`/api/important-senders/${encodeURIComponent(btn.dataset.key)}`, {
          method: 'DELETE',
        });
        await loadSettings();
      });
    });
  }

  async function loadSettings() {
    const [settingsRes, voicesRes] = await Promise.all([
      fetch('/api/settings'),
      fetch('/api/tts/voices').catch(() => ({ ok: false, json: async () => ({ ok: false }) })),
    ]);
    const settings = await settingsRes.json();
    const voices = voicesRes.ok ? await voicesRes.json() : { ok: false };

    const gmail = settings.gmail || {};
    if (gmail.connected) {
      gmailStatus.textContent = `Connected as ${gmail.email} (IMAP)`;
      gmailEmail.value = gmail.email || '';
      btnDisconnect.hidden = false;

    } else {
      gmailStatus.textContent = 'Not connected — enter Gmail and app password below';
      btnDisconnect.hidden = true;
    }

    const proton = settings.proton || {};
    if (proton.connected) {
      protonStatus.textContent = `Connected as ${proton.email} (Bridge IMAP)`;
      protonEmail.value = proton.email || '';
      btnProtonDisconnect.hidden = false;
    } else {
      protonStatus.textContent = 'Not connected — start Bridge and enter credentials below';
      btnProtonDisconnect.hidden = true;
    }

    pollInterval.value = String(settings.poll_interval || 60);
    alertCooldown.value = String(settings.alert_cooldown || 120);
    alertsEnabled.value = settings.alerts_enabled ? '1' : '0';
    if (importantAlertMode) {
      importantAlertMode.value = settings.important_alert_mode || 'always';
    }
    if (otherAlertMode) {
      otherAlertMode.value = settings.other_alert_mode || 'cooldown';
    }
    renderImportantList(settings.important_senders || []);

    if (voices.ok) {
      const voiceChoices = [];
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
      if (alertGreetingName) {
        alertGreetingName.value = voices.alert_greeting_name || '';
      }
      if (alertGreetingEnabled) {
        alertGreetingEnabled.value = voices.alert_greeting_enabled ? '1' : '0';
      }

      const chosen = voices.chosen;
      if (chosen) {
        voiceSelect.value = `${chosen.voice_mode}|${chosen.voice}`;
      }
    }
  }

  btnConnect.addEventListener('click', async () => {
    const email = gmailEmail.value.trim();
    const appPassword = gmailAppPassword.value.trim();
    if (!email || !appPassword) {
      gmailStatus.textContent = 'Enter Gmail address and app password.';
      return;
    }
    btnConnect.disabled = true;
    gmailStatus.textContent = 'Testing IMAP login…';
    try {
      const res = await fetch('/api/gmail/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, app_password: appPassword }),
      });
      const data = await res.json();
      if (data.ok) {
        gmailAppPassword.value = '';
        gmailStatus.textContent = `Connected as ${data.email}`;
        gmailStatus.className = 'gmail-status';
        btnDisconnect.hidden = false;
      } else {
        gmailStatus.textContent = data.error || 'Connection failed';
        gmailStatus.className = 'gmail-status error';
        console.error('Gmail connect failed:', data.error);
      }
    } catch (e) {
      gmailStatus.textContent = `Connection failed: ${e}`;
    } finally {
      btnConnect.disabled = false;
    }
  });

  btnDisconnect.addEventListener('click', async () => {
    await fetch('/api/gmail/disconnect', { method: 'POST' });
    gmailEmail.value = '';
    gmailAppPassword.value = '';
    await loadSettings();
  });

  btnProtonConnect.addEventListener('click', async () => {
    const email = protonEmail.value.trim();
    const password = protonPassword.value.trim();
    if (!email || !password) {
      protonStatus.textContent = 'Enter Proton address and Bridge IMAP password.';
      return;
    }
    btnProtonConnect.disabled = true;
    protonStatus.textContent = 'Testing Bridge IMAP login…';
    try {
      const res = await fetch('/api/mail/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: 'proton', email, password }),
      });
      const data = await res.json();
      if (data.ok) {
        protonPassword.value = '';
        protonStatus.textContent = `Connected as ${data.account.email}`;
        protonStatus.className = 'gmail-status';
        btnProtonDisconnect.hidden = false;
      } else {
        protonStatus.textContent = data.error || 'Connection failed';
        protonStatus.className = 'gmail-status error';
      }
    } catch (e) {
      protonStatus.textContent = `Connection failed: ${e}`;
    } finally {
      btnProtonConnect.disabled = false;
    }
  });

  btnProtonDisconnect.addEventListener('click', async () => {
    await fetch('/api/mail/disconnect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: 'proton' }),
    });
    protonEmail.value = '';
    protonPassword.value = '';
    await loadSettings();
  });

  document.getElementById('btn-save-important').addEventListener('click', async () => {
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        important_alert_mode: importantAlertMode.value,
        other_alert_mode: otherAlertMode.value,
      }),
    });
    gmailStatus.textContent = 'Important sender alert rules saved.';
  });

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
    const greetingName = alertGreetingName ? alertGreetingName.value.trim() : '';
    const greetingOn = alertGreetingEnabled ? alertGreetingEnabled.value === '1' : false;
    if (greetingOn && !greetingName) {
      gmailStatus.textContent = 'Enter your name to enable greetings.';
      return;
    }
    await fetch('/api/tts/voice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        voice_mode: mode,
        voice: voice,
        delivery_mode: deliveryMode.value,
        tts_model: ttsModel.value,
        alert_greeting_name: greetingName,
        alert_greeting_enabled: greetingOn,
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

  loadSettings();
})();