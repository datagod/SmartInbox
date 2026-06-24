(function () {
  const ollamaStatus = document.getElementById('ollama-status');
  const ollamaBaseUrl = document.getElementById('ollama-base-url');
  const promptsDir = document.getElementById('prompts-dir');
  const modelSingle = document.getElementById('model-single');
  const modelName = document.getElementById('model-name');
  const modelPicker = document.getElementById('model-picker');
  const modelSelect = document.getElementById('model-select');
  const modelActions = document.getElementById('model-actions');
  const systemPrompt = document.getElementById('system-prompt');
  const promptStatus = document.getElementById('prompt-status');
  const savedPromptSelect = document.getElementById('saved-prompt-select');
  const promptSaveName = document.getElementById('prompt-save-name');
  const btnSaveModel = document.getElementById('btn-save-model');
  const btnSavePrompt = document.getElementById('btn-save-prompt');
  const btnSavePromptFile = document.getElementById('btn-save-prompt-file');
  const btnOverwritePromptFile = document.getElementById('btn-overwrite-prompt-file');
  const btnResetPrompt = document.getElementById('btn-reset-prompt');
  const btnLoadPrompt = document.getElementById('btn-load-prompt');
  const btnPreviewPrompt = document.getElementById('btn-preview-prompt');
  const btnDeletePromptFile = document.getElementById('btn-delete-prompt-file');

  let state = null;
  let promptDirty = false;

  function setStatus(el, text, isError) {
    el.textContent = text;
    el.className = isError ? 'gmail-status error' : 'gmail-status';
  }

  function selectedPromptFilename() {
    return savedPromptSelect ? savedPromptSelect.value : '';
  }

  function updatePromptStatus() {
    if (!state) return;
    if (promptDirty) {
      promptStatus.textContent = 'Unsaved changes in editor';
      promptStatus.className = 'llm-prompt-status dirty';
      return;
    }
    if (state.prompt_source === 'file' && state.active_prompt_file) {
      promptStatus.textContent = `Active prompt from ${state.active_prompt_file}`;
      promptStatus.className = 'llm-prompt-status custom';
      return;
    }
    if (state.is_custom_prompt) {
      promptStatus.textContent = 'Active custom prompt (edited in app, not linked to a file)';
      promptStatus.className = 'llm-prompt-status custom';
      return;
    }
    promptStatus.textContent = 'Using the built-in default prompt';
    promptStatus.className = 'llm-prompt-status default';
  }

  function renderSavedPrompts(data) {
    if (!savedPromptSelect) return;
    const prompts = data.saved_prompts || [];
    if (!prompts.length) {
      savedPromptSelect.innerHTML = '<option value="">No saved prompts yet</option>';
      if (btnOverwritePromptFile) btnOverwritePromptFile.disabled = true;
      if (btnDeletePromptFile) btnDeletePromptFile.disabled = true;
      return;
    }
    savedPromptSelect.innerHTML = prompts
      .map((p) => {
        const label = p.is_default ? `${p.label} (default file)` : p.label;
        const selected =
          p.filename === data.active_prompt_file ? ' selected' : '';
        return `<option value="${p.filename}"${selected}>${label}</option>`;
      })
      .join('');
    const hasSelection = !!selectedPromptFilename();
    if (btnOverwritePromptFile) btnOverwritePromptFile.disabled = !hasSelection;
    if (btnDeletePromptFile) {
      const file = selectedPromptFilename();
      btnDeletePromptFile.disabled = !file || file === 'default.txt';
    }
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
    if (promptsDir && data.prompts_dir) {
      promptsDir.textContent = data.prompts_dir;
    }
    renderSavedPrompts(data);
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

  savedPromptSelect?.addEventListener('change', () => {
    const file = selectedPromptFilename();
    if (btnOverwritePromptFile) btnOverwritePromptFile.disabled = !file;
    if (btnDeletePromptFile) {
      btnDeletePromptFile.disabled = !file || file === 'default.txt';
    }
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

  btnSavePromptFile?.addEventListener('click', async () => {
    const prompt = systemPrompt.value.trim();
    const name = promptSaveName ? promptSaveName.value.trim() : '';
    if (!prompt) {
      setStatus(promptStatus, 'Prompt cannot be empty', true);
      return;
    }
    if (!name) {
      setStatus(promptStatus, 'Enter a name for the saved prompt file', true);
      return;
    }
    btnSavePromptFile.disabled = true;
    try {
      const res = await fetch('/api/llm/prompts/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, prompt }),
      });
      const data = await res.json();
      if (!data.ok) {
        promptStatus.textContent = data.error || 'Failed to save prompt file';
        promptStatus.className = 'llm-prompt-status dirty';
        return;
      }
      if (promptSaveName) promptSaveName.value = '';
      promptStatus.textContent = `Saved to ${data.filename}`;
      promptStatus.className = 'llm-prompt-status custom';
      await loadLlm();
      if (savedPromptSelect && data.filename) {
        savedPromptSelect.value = data.filename;
      }
    } finally {
      btnSavePromptFile.disabled = false;
    }
  });

  btnOverwritePromptFile?.addEventListener('click', async () => {
    const filename = selectedPromptFilename();
    const prompt = systemPrompt.value.trim();
    if (!filename || !prompt) return;
    if (!confirm(`Overwrite ${filename} with the current editor text?`)) return;
    btnOverwritePromptFile.disabled = true;
    try {
      const res = await fetch('/api/llm/prompts/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename, prompt, overwrite: true }),
      });
      const data = await res.json();
      if (!data.ok) {
        promptStatus.textContent = data.error || 'Failed to update prompt file';
        promptStatus.className = 'llm-prompt-status dirty';
        return;
      }
      promptStatus.textContent = `Updated ${data.filename}`;
      promptStatus.className = 'llm-prompt-status custom';
      await loadLlm();
    } finally {
      btnOverwritePromptFile.disabled = false;
    }
  });

  btnLoadPrompt?.addEventListener('click', async () => {
    const filename = selectedPromptFilename();
    if (!filename) return;
    btnLoadPrompt.disabled = true;
    try {
      const res = await fetch('/api/llm/prompts/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename }),
      });
      const data = await res.json();
      if (!data.ok) {
        promptStatus.textContent = data.error || 'Failed to load prompt';
        promptStatus.className = 'llm-prompt-status dirty';
        return;
      }
      promptDirty = false;
      systemPrompt.value = data.prompt || '';
      await loadLlm();
    } finally {
      btnLoadPrompt.disabled = false;
    }
  });

  btnPreviewPrompt?.addEventListener('click', async () => {
    const filename = selectedPromptFilename();
    if (!filename) return;
    btnPreviewPrompt.disabled = true;
    try {
      const res = await fetch(`/api/llm/prompts/${encodeURIComponent(filename)}`);
      const data = await res.json();
      if (!data.ok) {
        promptStatus.textContent = data.error || 'Failed to read prompt file';
        promptStatus.className = 'llm-prompt-status dirty';
        return;
      }
      promptDirty = true;
      systemPrompt.value = data.prompt || '';
      updatePromptStatus();
      btnResetPrompt.disabled = false;
      promptStatus.textContent = `Previewing ${filename} (not active until you click Load or Use this prompt)`;
      promptStatus.className = 'llm-prompt-status dirty';
    } finally {
      btnPreviewPrompt.disabled = false;
    }
  });

  btnDeletePromptFile?.addEventListener('click', async () => {
    const filename = selectedPromptFilename();
    if (!filename || filename === 'default.txt') return;
    if (!confirm(`Delete ${filename}?`)) return;
    btnDeletePromptFile.disabled = true;
    try {
      const res = await fetch(`/api/llm/prompts/${encodeURIComponent(filename)}`, {
        method: 'DELETE',
      });
      const data = await res.json();
      if (!data.ok) {
        promptStatus.textContent = data.error || 'Failed to delete prompt file';
        promptStatus.className = 'llm-prompt-status dirty';
        return;
      }
      await loadLlm();
    } finally {
      btnDeletePromptFile.disabled = false;
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