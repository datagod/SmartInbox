(function () {
  const ollamaStatus = document.getElementById('ollama-status');
  const ollamaBaseUrl = document.getElementById('ollama-base-url');
  const modelSingle = document.getElementById('model-single');
  const modelName = document.getElementById('model-name');
  const modelPicker = document.getElementById('model-picker');
  const modelSelect = document.getElementById('model-select');
  const modelActions = document.getElementById('model-actions');
  const systemPrompt = document.getElementById('system-prompt');
  const promptStatus = document.getElementById('prompt-status');
  const btnSaveModel = document.getElementById('btn-save-model');
  const btnSavePrompt = document.getElementById('btn-save-prompt');
  const btnResetPrompt = document.getElementById('btn-reset-prompt');

  let state = null;
  let promptDirty = false;

  function setStatus(el, text, isError) {
    el.textContent = text;
    el.className = isError ? 'gmail-status error' : 'gmail-status';
  }

  function updatePromptStatus() {
    if (!state) return;
    if (promptDirty) {
      promptStatus.textContent = 'Unsaved changes';
      promptStatus.className = 'llm-prompt-status dirty';
      return;
    }
    if (state.is_custom_prompt) {
      promptStatus.textContent = 'Using your saved custom prompt';
      promptStatus.className = 'llm-prompt-status custom';
      return;
    }
    promptStatus.textContent = 'Using the default prompt';
    promptStatus.className = 'llm-prompt-status default';
  }

  function renderModels(data) {
    const models = data.models || [];
    ollamaBaseUrl.textContent = data.base_url || '';

    if (!data.reachable) {
      modelSingle.hidden = true;
      modelPicker.hidden = true;
      modelActions.hidden = true;
      setStatus(ollamaStatus, `Ollama unreachable — ${data.error || 'check config'}`, true);
      return;
    }

    const listed = data.model_listed;
    const selected = data.selected_model || '';
    if (listed) {
      setStatus(ollamaStatus, `Ollama reachable — ${models.length} model(s) available`, false);
    } else {
      setStatus(
        ollamaStatus,
        `Ollama reachable — selected model "${selected}" is not loaded`,
        true
      );
    }

    if (models.length <= 1) {
      modelSingle.hidden = false;
      modelPicker.hidden = true;
      modelActions.hidden = true;
      modelName.textContent = models.length ? models[0].name : selected || '(none)';
      return;
    }

    modelSingle.hidden = true;
    modelPicker.hidden = false;
    modelActions.hidden = false;
    modelSelect.innerHTML = models
      .map((m) => {
        const name = m.name;
        const selectedAttr = name === selected ? ' selected' : '';
        return `<option value="${name}"${selectedAttr}>${name}</option>`;
      })
      .join('');
    if (!modelSelect.value && selected) {
      const opt = document.createElement('option');
      opt.value = selected;
      opt.textContent = selected;
      opt.selected = true;
      modelSelect.appendChild(opt);
    }
  }

  function renderPrompt(data) {
    if (!promptDirty) {
      systemPrompt.value = data.system_prompt || '';
    }
    updatePromptStatus();
    btnResetPrompt.disabled = !data.is_custom_prompt && !promptDirty;
  }

  async function loadLlm() {
    const res = await fetch('/api/llm');
    state = await res.json();
    renderModels(state);
    renderPrompt(state);
  }

  systemPrompt.addEventListener('input', () => {
    promptDirty = true;
    updatePromptStatus();
    btnResetPrompt.disabled = false;
  });

  btnSaveModel.addEventListener('click', async () => {
    const model = modelSelect.value;
    btnSaveModel.disabled = true;
    try {
      const res = await fetch('/api/llm/model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      });
      const data = await res.json();
      if (!data.ok) {
        setStatus(ollamaStatus, data.error || 'Failed to save model', true);
        return;
      }
      promptDirty = false;
      await loadLlm();
      setStatus(ollamaStatus, `Model saved: ${data.model}`, false);
    } finally {
      btnSaveModel.disabled = false;
    }
  });

  btnSavePrompt.addEventListener('click', async () => {
    const prompt = systemPrompt.value.trim();
    if (!prompt) {
      setStatus(promptStatus, 'Prompt cannot be empty', true);
      return;
    }
    btnSavePrompt.disabled = true;
    try {
      const res = await fetch('/api/llm/prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      const data = await res.json();
      if (!data.ok) {
        promptStatus.textContent = data.error || 'Failed to save prompt';
        promptStatus.className = 'llm-prompt-status dirty';
        return;
      }
      promptDirty = false;
      await loadLlm();
    } finally {
      btnSavePrompt.disabled = false;
    }
  });

  btnResetPrompt.addEventListener('click', async () => {
    btnResetPrompt.disabled = true;
    try {
      const res = await fetch('/api/llm/prompt/reset', { method: 'POST' });
      const data = await res.json();
      if (!data.ok) {
        promptStatus.textContent = data.error || 'Failed to reset prompt';
        promptStatus.className = 'llm-prompt-status dirty';
        return;
      }
      promptDirty = false;
      systemPrompt.value = data.prompt || '';
      await loadLlm();
    } finally {
      btnResetPrompt.disabled = false;
    }
  });

  loadLlm();
})();