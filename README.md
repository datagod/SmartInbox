# SmartInbox

Gmail inbox monitor with **Ollama** summaries and **Chatterbox** voice alerts — styled like MultiTone Radio Chat, with frigate-tui's delivery modes (Conspiracy, Neurotic, Playful, and more).

**Created with Grok Build**

## Features

- **Gmail via IMAP + App Password** — enter your address and a Google App Password in Settings (no OAuth, no Cloud Console)
- **Ollama summaries** — local LLM summarizes each new email
- **Chatterbox TTS** — spoken alerts with clone voices and delivery modes
- **Settings tab** — poll interval, alert cooldown, voice, delivery mode
- **Three-panel UI** — Inbox, Summary, Activity Log (dark MTRC-style theme)

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com/) running locally (e.g. `qwen2.5:3b`)
- [Chatterbox TTS Server](https://github.com/devnen/Chatterbox-TTS-Server) on port 8004
- Gmail with **2-Step Verification** and an **App Password**

## Gmail setup (one-time)

1. Turn on [2-Step Verification](https://myaccount.google.com/signinoptions/two-step-verification)
2. Create an [App Password](https://myaccount.google.com/apppasswords) (Mail → Other → `SmartInbox`)
3. In SmartInbox **Settings**, enter your Gmail address and the 16-character app password

**Note:** Use the app password, not your regular Gmail password.

## Quick start

```bash
git clone https://github.com/datagod/SmartInbox.git
cd SmartInbox
cp config.example.yaml config.yaml
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/smartinbox
```

Open **http://127.0.0.1:8090** → **Settings** → enter Gmail + app password → **Save & connect**.

## Configuration

See `config.example.yaml` for Ollama URL, Chatterbox URL, default poll interval, and alert template.

Gmail credentials are stored in `data/smartinbox.db` (local SQLite, gitignored).

## Security

**Never commit secrets.** The following stay on your machine only:

| File / directory | Contains |
|------------------|----------|
| `data/smartinbox.db` | Gmail app password, emails, summaries |
| `config.yaml` | Your local URLs and preferences |
| `localrecordings/` | Cached Chatterbox audio |

Before pushing:

```bash
git status
./scripts/check-no-secrets.sh
```

## License

MIT