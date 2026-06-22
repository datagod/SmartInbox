# SmartInbox

Gmail inbox monitor with **Ollama** summaries and **Chatterbox** voice alerts — styled like MultiTone Radio Chat, with frigate-tui's delivery modes (Conspiracy, Neurotic, Playful, and more).

**Created with Grok Build**

## Features

- **Gmail OAuth** — connect via Google sign-in; no passwords in the UI
- **Ollama summaries** — local LLM summarizes each new email
- **Chatterbox TTS** — spoken alerts with clone voices and delivery modes
- **Settings tab** — poll interval, alert cooldown, voice, delivery mode
- **Three-panel UI** — Inbox, Summary, Activity Log (dark MTRC-style theme)

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com/) running locally (e.g. `qwen2.5:3b`)
- [Chatterbox TTS Server](https://github.com/devnen/Chatterbox-TTS-Server) on port 8004
- Google Cloud project with **Gmail API** enabled and OAuth 2.0 Web credentials

## Quick start

```bash
git clone https://github.com/datagod/SmartInbox.git
cd SmartInbox
cp config.example.yaml config.yaml
cp .env.example .env
# Edit .env with GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
pip install -e .
smartinbox
```

Open **http://127.0.0.1:8090** → **Settings** → **Connect Gmail**.

### Google Cloud setup

1. Create a project at [Google Cloud Console](https://console.cloud.google.com/)
2. Enable **Gmail API**
3. Create **OAuth 2.0 Client ID** (Web application)
4. Add authorized redirect URI: `http://127.0.0.1:8090/api/auth/google/callback`
5. Copy client ID and secret into `.env`

## Configuration

See `config.example.yaml` for Ollama URL, Chatterbox URL, default poll interval, and alert template.

Runtime overrides (poll interval, cooldown, voice, delivery mode) are saved in `localrecordings/.event_voice.json` via the Settings UI.

## Security

**Never commit secrets.** The following stay on your machine only:

| File / directory | Contains |
|------------------|----------|
| `.env` | Google OAuth client ID and secret |
| `config.yaml` | Your local URLs and preferences |
| `data/` | SQLite DB with Gmail OAuth refresh tokens and email content |
| `localrecordings/` | Cached Chatterbox audio |

Only `.env.example` and `config.example.yaml` belong in git (placeholders, no real values).

Before pushing, verify nothing sensitive is staged:

```bash
git status
git diff --cached
```

If you ever accidentally commit a secret, rotate it in Google Cloud Console immediately and remove it from git history.

## License

MIT