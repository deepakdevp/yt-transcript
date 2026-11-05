# YouTube Transcript & Summarizer

Local web app. Paste a YouTube link, get the transcript, copy it or have Claude summarize it.

## Run

    ~/tools/yt-transcript/yt

Opens http://localhost:8420. Ctrl-C to stop.

## What it does

- Accepts any YouTube URL shape: `watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, `/live/`, or a bare video ID
- Shows the transcript one cue per line; every timestamp links back into the video at that moment
- **Copy** (with timestamps) / **Copy plain** (one paragraph, best for pasting into an LLM) / **Download .txt**
- **Summarize** via the `claude` CLI — three styles: Detailed (topics + timestamps), Quick (TL;DR), Key points
- Filter box searches the transcript; press `/` to jump to it
- Switches caption language when a video has more than one track
- Caches fetched transcripts in `~/.cache/yt-transcript` for 30 days

## Keyboard

| Key | Action |
|---|---|
| `⌘`+`↵` | Fetch transcript |
| `/` | Focus the filter box |

## Config

| Env var | Default | Meaning |
|---|---|---|
| `YT_PORT` | `8420` | Port to serve on |
| `YT_CLAUDE_BIN` | `claude` | Path to the Claude CLI |
| `YT_CLAUDE_MODEL` | *(CLI default)* | e.g. `sonnet` for faster summaries |

## Tests

    .venv/bin/python -m pytest tests/ -q

## Limits

- Only works for videos that already have captions (about 85% of YouTube). No captions -> clear error, no Whisper fallback.
- Age-restricted and private videos need a signed-in session, which this does not use.
- Fetching runs from your own machine on purpose — YouTube blocks datacenter IPs.

## License

MIT — see [LICENSE](LICENSE).
