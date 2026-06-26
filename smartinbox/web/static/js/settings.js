(function () {
  const gmailStatus = document.getElementById('gmail-status');
  const gmailEmail = document.getElementById('gmail-email');
  const gmailAppPassword = document.getElementById('gmail-app-password');
  const btnConnect = document.getElementById('btn-connect');
  const btnDisconnect = document.getElementById('btn-disconnect');
  const pollInterval = document.getElementById('poll-interval');
  const alertCooldown = document.getElementById('alert-cooldown');
  const calendarExtractConcurrency = document.getElementById('calendar-extract-concurrency');
  const calendarOllamaGpu = document.getElementById('calendar-ollama-gpu');
  const alertsEnabled = document.getElementById('alerts-enabled');
  const voiceSelect = document.getElementById('voice-select');
  const ttsModel = document.getElementById('tts-model');
  const deliveryMode = document.getElementById('delivery-mode');
  const alertGreetingName = document.getElementById('alert-greeting-name');
  const alertGreetingEnabled = document.getElementById('alert-greeting-enabled');
  const voiceSummarySwitcher = document.getElementById('voice-summary-switcher');
  const demoModeSwitcher = document.getElementById('demo-mode-switcher');
  const voiceStylePrompt = document.getElementById('voice-style-prompt');
  const voiceStylePromptSelect = document.getElementById('voice-style-prompt-select');
  const voiceStylePromptName = document.getElementById('voice-style-prompt-name');
  const importantAlertMode = document.getElementById('important-alert-mode');
  const otherAlertMode = document.getElementById('other-alert-mode');
  const importantList = document.getElementById('important-list');
  const protonStatus = document.getElementById('proton-status');
  const protonEmail = document.getElementById('proton-email');
  const protonPassword = document.getElementById('proton-password');
  const btnProtonConnect = document.getElementById('btn-proton-connect');
  const btnProtonDisconnect = document.getElementById('btn-proton-disconnect');
  const btnTestSpeak = document.getElementById('btn-test-speak');
  const settingsActivityLog = document.getElementById('settings-activity-log');
  const btnClearSettingsActivityLog = document.getElementById('btn-clear-settings-activity-log');

  let voiceSummaryEnabled = false;
  let voiceStyleDefaultPrompt = '';

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
    const lvl = (entry.level || 'info').replace('warning', 'warn');
    const isInboxCheck = String(entry.message || '').startsWith('Inbox check —');
    div.className = isInboxCheck ? 'activity-entry activity-entry--success' : 'activity-entry';
    const tsClass = isInboxCheck ? 'success' : lvl;
    div.innerHTML = `<span class="lvl-${tsClass}">[${entry.ts}]</span> ${escapeHtml(entry.message)}`;
    return div;
  }

  function renderActivityLog(entries) {
    if (!settingsActivityLog) return;
    settingsActivityLog.innerHTML = '';
    [...(entries || [])]
      .sort(compareLogsDesc)
      .slice(0, 120)
      .forEach((entry) => {
        settingsActivityLog.appendChild(buildLogElement(entry));
      });
  }

  function appendActivityLog(entry) {
    if (!settingsActivityLog || !entry) return;
    settingsActivityLog.prepend(buildLogElement(entry));
    while (settingsActivityLog.children.length > 120) {
      settingsActivityLog.removeChild(settingsActivityLog.lastChild);
    }
  }

  function connectActivityStream() {
    const es = new EventSource('/api/stream');
    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'snapshot') renderActivityLog(msg.data?.logs || []);
        if (msg.type === 'log') appendActivityLog(msg.data);
        if (msg.type === 'logs') renderActivityLog(msg.data || []);
      } catch (_) { /* ignore */ }
    };
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function setSwitcherValue(switcher, enabled) {
    if (!switcher) return;
    switcher.querySelectorAll('.settings-view-option').forEach((btn) => {
      const on = btn.dataset.value === (enabled ? 'on' : 'off');
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function readSwitcherValue(switcher) {
    if (!switcher) return false;
    const active = switcher.querySelector('.settings-view-option.is-active');
    return active ? active.dataset.value === 'on' : false;
  }

  function wireSwitcher(switcher, onChange) {
    if (!switcher) return;
    switcher.querySelectorAll('.settings-view-option').forEach((btn) => {
      btn.addEventListener('click', () => {
        const enabled = btn.dataset.value === 'on';
        setSwitcherValue(switcher, enabled);
        onChange(enabled);
      });
    });
  }

  function applyChatterboxVoicePrefs(chatter) {
    const cfg = chatter || {};
    voiceSummaryEnabled = !!cfg.voice_summary_enabled;
    setSwitcherValue(voiceSummarySwitcher, voiceSummaryEnabled);
    if (deliveryMode && cfg.delivery_mode) {
      deliveryMode.value = cfg.delivery_mode;
    }
    if (alertGreetingName && cfg.alert_greeting_name != null) {
      alertGreetingName.value = cfg.alert_greeting_name || '';
    }
    if (alertGreetingEnabled) {
      alertGreetingEnabled.value = cfg.alert_greeting_enabled ? '1' : '0';
    }
    if (ttsModel && cfg.tts_model) {
      ttsModel.value = cfg.tts_model;
    }
    voiceStyleDefaultPrompt = cfg.voice_style_default_prompt || voiceStyleDefaultPrompt;
    if (voiceStylePrompt && cfg.voice_style_prompt) {
      voiceStylePrompt.value = cfg.voice_style_prompt;
    }
    renderVoiceStylePromptSelect(
      cfg.saved_voice_style_prompts,
      cfg.voice_style_prompt_file
    );
  }

  function renderVoiceStylePromptSelect(prompts, activeFile) {
    if (!voiceStylePromptSelect) return;
    const items = prompts || [];
    if (!items.length) {
      voiceStylePromptSelect.innerHTML = '<option value="">(no saved prompts)</option>';
      return;
    }
    voiceStylePromptSelect.innerHTML = items
      .map((row) => {
        const file = row.filename || '';
        const label = row.label || file;
        const selected = file === activeFile ? ' selected' : '';
        return `<option value="${escapeHtml(file)}"${selected}>${escapeHtml(label)}</option>`;
      })
      .join('');
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

  async function loadVoiceStylePromptLibrary() {
    if (!voiceStylePromptSelect) return;
    try {
      const res = await fetch('/api/tts/voice-style-prompts');
      if (!res.ok) {
        voiceStylePromptSelect.innerHTML = '<option value="">(prompt API unavailable)</option>';
        return;
      }
      const data = await res.json();
      if (!data.ok) {
        voiceStylePromptSelect.innerHTML = '<option value="">(failed to load prompts)</option>';
        return;
      }
      voiceStyleDefaultPrompt = data.default_prompt || voiceStyleDefaultPrompt;
      renderVoiceStylePromptSelect(data.saved_prompts, data.active_prompt_file);
      if (voiceStylePrompt && data.prompt) {
        voiceStylePrompt.value = data.prompt;
      }
    } catch (_) {
      voiceStylePromptSelect.innerHTML = '<option value="">(failed to load prompts)</option>';
    }
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
    if (calendarExtractConcurrency) {
      calendarExtractConcurrency.value = String(settings.calendar_extract_concurrency || 6);
    }
    if (calendarOllamaGpu) {
      const gpu = settings.calendar_ollama_main_gpu;
      calendarOllamaGpu.value = gpu == null || gpu < 0 ? '' : String(gpu);
    }
    alertsEnabled.value = settings.alerts_enabled ? '1' : '0';
    if (importantAlertMode) {
      importantAlertMode.value = settings.important_alert_mode || 'always';
    }
    if (otherAlertMode) {
      otherAlertMode.value = settings.other_alert_mode || 'cooldown';
    }
    renderImportantList(settings.important_senders || []);
    setSwitcherValue(demoModeSwitcher, !!settings.demo_mode);
    applyChatterboxVoicePrefs(settings.chatterbox_tts);

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
      deliveryMode.value = voices.delivery_mode || deliveryMode.value || 'normal';
      if (alertGreetingName) {
        alertGreetingName.value = voices.alert_greeting_name || alertGreetingName.value || '';
      }
      if (alertGreetingEnabled) {
        alertGreetingEnabled.value = voices.alert_greeting_enabled ? '1' : '0';
      }
      voiceSummaryEnabled = !!voices.voice_summary_enabled;
      setSwitcherValue(voiceSummarySwitcher, voiceSummaryEnabled);

      const chosen = voices.chosen;
      if (chosen) {
        voiceSelect.value = `${chosen.voice_mode}|${chosen.voice}`;
      }
    }

    await loadVoiceStylePromptLibrary();
  }

  wireSwitcher(voiceSummarySwitcher, (enabled) => {
    voiceSummaryEnabled = enabled;
  });

  wireSwitcher(demoModeSwitcher, async (enabled) => {
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ demo_mode: enabled }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        setSwitcherValue(demoModeSwitcher, !enabled);
        gmailStatus.textContent = data.error || 'Failed to save demo mode';
        gmailStatus.className = 'gmail-status error';
        return;
      }
      gmailStatus.textContent = enabled
        ? 'Demo mode enabled — open Inbox for sample emails'
        : 'Demo mode disabled — showing live inbox';
      gmailStatus.className = 'gmail-status';
    } catch (e) {
      setSwitcherValue(demoModeSwitcher, !enabled);
      gmailStatus.textContent = `Failed to save demo mode: ${e}`;
      gmailStatus.className = 'gmail-status error';
    }
  });

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

  document.getElementById('btn-save-calendar-settings')?.addEventListener('click', async () => {
    const concurrency = Number(calendarExtractConcurrency?.value || 6);
    const gpuRaw = calendarOllamaGpu?.value?.trim();
    const gpuPayload = gpuRaw === '' || gpuRaw == null ? 'auto' : Number(gpuRaw);
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          calendar_extract_concurrency: concurrency,
          calendar_ollama_main_gpu: gpuPayload,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        gmailStatus.textContent = data.error || 'Failed to save calendar settings';
        gmailStatus.className = 'gmail-status error';
        return;
      }
      if (calendarExtractConcurrency) {
        calendarExtractConcurrency.value = String(data.calendar_extract_concurrency || concurrency);
      }
      if (calendarOllamaGpu) {
        const gpu = data.calendar_ollama_main_gpu;
        calendarOllamaGpu.value = gpu == null || gpu < 0 ? '' : String(gpu);
      }
      const gpuLabel =
        data.calendar_ollama_main_gpu == null ? 'auto GPU' : `GPU ${data.calendar_ollama_main_gpu}`;
      gmailStatus.textContent =
        `Calendar settings saved (${data.calendar_extract_concurrency} parallel, ${gpuLabel}).`;
      gmailStatus.className = 'gmail-status';
    } catch (e) {
      gmailStatus.textContent = `Failed to save calendar settings: ${e}`;
      gmailStatus.className = 'gmail-status error';
    }
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
    const promptText = voiceStylePrompt ? voiceStylePrompt.value.trim() : '';
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
        voice_summary_enabled: voiceSummaryEnabled,
        voice_style_prompt: promptText || undefined,
      }),
    });
    gmailStatus.textContent = 'Voice settings saved.';
    await loadVoiceStylePromptLibrary();
  });

  if (btnTestSpeak) btnTestSpeak.addEventListener('click', async () => {
    const [mode, voice] = voiceSelect.value.split('|');
    const greetingName = alertGreetingName ? alertGreetingName.value.trim() : '';
    const greetingOn = alertGreetingEnabled ? alertGreetingEnabled.value === '1' : false;
    const promptText = voiceStylePrompt ? voiceStylePrompt.value.trim() : '';
    const promptFile = voiceStylePromptSelect ? voiceStylePromptSelect.value.trim() : '';
    if (greetingOn && !greetingName) {
      gmailStatus.textContent = 'Enter your name to test greetings.';
      return;
    }
    gmailStatus.textContent = 'Preparing test speech…';
    btnTestSpeak.disabled = true;
    const now = new Date();
    const ts = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    appendActivityLog({
      ts,
      at: now.getTime() / 1000,
      level: 'info',
      message: 'Test speak — button clicked (waiting for server…)',
    });
    try {
      const res = await fetch('/api/tts/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voice_mode: mode,
          voice: voice,
          delivery_mode: deliveryMode.value,
          tts_model: ttsModel.value,
          alert_greeting_name: greetingName,
          alert_greeting_enabled: greetingOn,
          voice_summary_enabled: voiceSummaryEnabled,
          voice_style_prompt: promptText || undefined,
          voice_style_prompt_file: promptFile || undefined,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        gmailStatus.textContent = err.error || 'TTS test failed';
        return;
      }
      const blob = await res.blob();
      const audio = new Audio(URL.createObjectURL(blob));
      gmailStatus.textContent = 'Playing test speech…';
      await audio.play();
      gmailStatus.textContent = 'Test speech complete.';
    } catch (e) {
      gmailStatus.textContent = `TTS test failed: ${e}`;
    } finally {
      btnTestSpeak.disabled = false;
    }
  });

  const btnLoadVoiceStylePrompt = document.getElementById('btn-load-voice-style-prompt');
  if (btnLoadVoiceStylePrompt) {
    btnLoadVoiceStylePrompt.addEventListener('click', async () => {
      const filename = voiceStylePromptSelect ? voiceStylePromptSelect.value : '';
      if (!filename) {
        gmailStatus.textContent = 'Select a saved voice style prompt first.';
        return;
      }
      const res = await fetch('/api/tts/voice-style-prompts/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename }),
      });
      const data = await res.json();
      if (!data.ok) {
        gmailStatus.textContent = data.error || 'Load failed';
        return;
      }
      if (voiceStylePrompt) voiceStylePrompt.value = data.prompt || '';
      gmailStatus.textContent = `Loaded voice style prompt: ${filename}`;
      await loadVoiceStylePromptLibrary();
    });
  }

  const btnSaveVoiceStylePromptFile = document.getElementById('btn-save-voice-style-prompt-file');
  if (btnSaveVoiceStylePromptFile) {
    btnSaveVoiceStylePromptFile.addEventListener('click', async () => {
      const prompt = voiceStylePrompt ? voiceStylePrompt.value.trim() : '';
      const name = voiceStylePromptName ? voiceStylePromptName.value.trim() : '';
      if (!prompt) {
        gmailStatus.textContent = 'Enter a voice style prompt first.';
        return;
      }
      const res = await fetch('/api/tts/voice-style-prompts/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name || 'voice_style', prompt }),
      });
      const data = await res.json();
      if (!data.ok) {
        gmailStatus.textContent = data.error || 'Save failed';
        return;
      }
      gmailStatus.textContent = `Saved voice style prompt: ${data.filename}`;
      await loadVoiceStylePromptLibrary();
    });
  }

  const btnResetVoiceStylePrompt = document.getElementById('btn-reset-voice-style-prompt');
  if (btnResetVoiceStylePrompt) {
    btnResetVoiceStylePrompt.addEventListener('click', () => {
      if (voiceStylePrompt) {
        voiceStylePrompt.value = voiceStyleDefaultPrompt || voiceStylePrompt.value;
      }
      gmailStatus.textContent = 'Voice style prompt reset to built-in default (save to apply).';
    });
  }

  if (btnClearSettingsActivityLog) {
    btnClearSettingsActivityLog.addEventListener('click', async () => {
      btnClearSettingsActivityLog.disabled = true;
      try {
        await fetch('/api/logs/clear', { method: 'POST' });
        renderActivityLog([]);
      } finally {
        btnClearSettingsActivityLog.disabled = false;
      }
    });
  }

  connectActivityStream();
  loadSettings();
})();