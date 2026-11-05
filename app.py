"""FastAPI backend for the YouTube transcript tool."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import core

HERE = Path(__file__).parent
STATIC = HERE / "static"

# A long transcript still fits, but cap it so a 6-hour stream can't stall the CLI.
MAX_SUMMARY_CHARS = 180_000
SUMMARY_TIMEOUT = 300  # seconds

app = FastAPI(title="YouTube Transcript Tool", docs_url=None, redoc_url=None)


class TranscriptRequest(BaseModel):
    url: str = Field(..., max_length=2048)
    language: str | None = Field(default=None, max_length=16)
    refresh: bool = False


class SummaryRequest(BaseModel):
    text: str = Field(..., max_length=MAX_SUMMARY_CHARS + 1000)
    style: str = "detailed"
    video_id: str | None = Field(default=None, max_length=16)


def _error(message: str, hint: str = "", status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message, "hint": hint}, status_code=status)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "claude_cli": bool(shutil.which(CLAUDE_BIN))}


@app.post("/api/transcript")
def transcript(req: TranscriptRequest):
    try:
        video_id = core.parse_video_id(req.url)
    except ValueError as exc:
        return _error(str(exc), "Paste a link like https://youtu.be/VIDEOID")

    try:
        data = core.fetch(video_id, req.language, use_cache=not req.refresh)
    except core.TranscriptError as exc:
        return _error(exc.message, exc.hint)

    try:
        data["languages"] = core.list_languages(video_id)
    except core.TranscriptError:
        data["languages"] = []

    return data


# --- summarisation -------------------------------------------------------

CLAUDE_BIN = os.environ.get("YT_CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("YT_CLAUDE_MODEL", "")

STYLE_INSTRUCTIONS = {
    "quick": (
        "Write a TL;DR of at most 5 sentences, then 3-5 bullet points of the "
        "single most useful things a viewer would take away. Nothing else."
    ),
    "detailed": (
        "Write:\n"
        "1. **TL;DR** — two sentences.\n"
        "2. **Walkthrough** — the video broken into its natural topics, in order. "
        "Give each topic a `### [mm:ss] Heading` and 2-4 sentences underneath "
        "explaining what is actually said, not just what it is 'about'.\n"
        "3. **Key takeaways** — 4-6 bullets.\n"
        "4. **Worth skipping** — any stretch that is filler, sponsorship, or "
        "repetition, with its timestamp range. Omit this section if there is none."
    ),
    "keypoints": (
        "Write only a flat list of 8-15 bullet points capturing every distinct "
        "claim, step, or fact stated. Prefix each with its `[mm:ss]` timestamp. "
        "No intro, no conclusion."
    ),
}

PROMPT_TEMPLATE = """You are summarising the transcript of a YouTube video.

The transcript is auto-generated, so expect missing punctuation, run-on lines, and the odd misheard word. Read through those errors rather than quoting them.

{style}

Rules:
- Base every statement on the transcript. Never add outside knowledge, and never invent a timestamp.
- Timestamps in the transcript look like [mm:ss] or [h:mm:ss]. Reuse them exactly.
- Summarise and paraphrase in your own words. Quote at most one short sentence, and only if the exact wording matters.
- Output GitHub-flavoured Markdown. Start with the content itself — no preamble like "Here is the summary".

<transcript>
{transcript}
</transcript>
"""


@app.post("/api/summarize")
def summarize(req: SummaryRequest):
    text = (req.text or "").strip()
    if not text:
        return _error("Nothing to summarise.", "Fetch a transcript first.")

    if not shutil.which(CLAUDE_BIN):
        return _error(
            "Can't find the `claude` command.",
            f"Looked for {CLAUDE_BIN!r} on PATH. Set YT_CLAUDE_BIN to its full path.",
            status=503,
        )

    truncated = False
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS]
        truncated = True

    style = STYLE_INSTRUCTIONS.get(req.style, STYLE_INSTRUCTIONS["detailed"])
    prompt = PROMPT_TEMPLATE.format(style=style, transcript=text)

    cmd = [CLAUDE_BIN, "-p"]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=SUMMARY_TIMEOUT,
            cwd=str(HERE),
        )
    except subprocess.TimeoutExpired:
        return _error(
            "The summary took too long and was cancelled.",
            f"Gave up after {SUMMARY_TIMEOUT}s. Try the Quick style on a long video.",
            status=504,
        )
    except OSError as exc:
        return _error("Could not run the `claude` command.", str(exc)[:200], status=503)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return _error(
            "The `claude` command failed.",
            detail[-1][:300] if detail else f"Exit code {proc.returncode}.",
            status=502,
        )

    summary = (proc.stdout or "").strip()
    if not summary:
        return _error("The summariser returned nothing.", "Try again.", status=502)

    return {"summary": summary, "truncated": truncated, "style": req.style}
