"""Pure logic for fetching and formatting YouTube transcripts.

Everything above `fetch` is network-free and unit tested.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeRequestFailed,
)

CACHE_DIR = Path.home() / ".cache" / "yt-transcript"
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days

# An 11-char YouTube video id.
_ID = r"[A-Za-z0-9_-]{11}"

_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
    "www.youtu.be",
}

# /shorts/ID, /embed/ID, /live/ID, /v/ID, or youtu.be/ID
_PATH_RE = re.compile(rf"^/(?:shorts|embed|live|v|e)/({_ID})(?:[/?#]|$)")
_SHORT_RE = re.compile(rf"^/({_ID})(?:[/?#]|$)")
_BARE_RE = re.compile(rf"^{_ID}$")


class TranscriptError(Exception):
    """A user-facing failure. `.message` is safe to show in the UI."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class Segment:
    text: str
    start: float
    duration: float


def parse_video_id(url_or_id: str) -> str:
    """Pull an 11-character video id out of any common YouTube URL shape."""
    if not isinstance(url_or_id, str):
        raise ValueError("Expected a string.")

    raw = url_or_id.strip()
    if not raw:
        raise ValueError("No URL given.")

    if _BARE_RE.match(raw):
        return raw

    from urllib.parse import urlparse, parse_qs

    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
    except ValueError as exc:  # malformed IPv6 brackets, etc.
        raise ValueError("That doesn't look like a YouTube URL.") from exc

    if (parsed.hostname or "").lower() not in _HOSTS:
        raise ValueError("That doesn't look like a YouTube URL.")

    # /watch?v=ID
    v = parse_qs(parsed.query).get("v", [None])[0]
    if v and _BARE_RE.match(v):
        return v

    path = parsed.path or "/"
    match = _PATH_RE.match(path)
    if match:
        return match.group(1)

    if (parsed.hostname or "").lower().endswith("youtu.be"):
        match = _SHORT_RE.match(path)
        if match:
            return match.group(1)

    raise ValueError("Couldn't find a video ID in that URL.")


def fmt_timestamp(seconds: float) -> str:
    """0 -> '00:00', 3661 -> '1:01:01'. Truncates fractional seconds."""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _clean(text: str) -> str:
    return " ".join(text.split())


def to_plain_text(segments: Iterable[Segment]) -> str:
    """One paragraph, no timestamps — the shape you want to paste into an LLM."""
    return " ".join(part for part in (_clean(s.text) for s in segments) if part)


def to_timestamped_text(segments: Iterable[Segment]) -> str:
    """One line per caption cue, prefixed with a jumpable timestamp."""
    return "\n".join(
        f"[{fmt_timestamp(s.start)}] {_clean(s.text)}" for s in segments
    )


# --- network layer -------------------------------------------------------


def _cache_path(video_id: str, language: str | None) -> Path:
    suffix = language or "auto"
    return CACHE_DIR / f"{video_id}.{suffix}.json"


def _read_cache(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), "utf-8")
    except OSError:
        pass  # a cache miss is never worth failing the request over


def _to_friendly(exc: Exception) -> TranscriptError:
    if isinstance(exc, TranscriptsDisabled):
        return TranscriptError(
            "This video has captions turned off.",
            hint="The uploader disabled them — there is no transcript to fetch.",
        )
    if isinstance(exc, NoTranscriptFound):
        return TranscriptError(
            "No transcript in that language.",
            hint="Try another language from the dropdown.",
        )
    if isinstance(exc, (IpBlocked, RequestBlocked)):
        return TranscriptError(
            "YouTube is blocking requests from this network.",
            hint="Wait a few minutes, or switch off a VPN if you have one on.",
        )
    if isinstance(exc, AgeRestricted):
        return TranscriptError(
            "This video is age-restricted.",
            hint="Age-restricted videos need a signed-in session, which this tool does not use.",
        )
    if isinstance(exc, (InvalidVideoId, VideoUnavailable)):
        return TranscriptError(
            "That video is unavailable.",
            hint="It may be private, deleted, or region-locked.",
        )
    if isinstance(exc, VideoUnplayable):
        return TranscriptError(
            "YouTube will not play this video.",
            hint=str(exc).splitlines()[0][:200] if str(exc) else "",
        )
    if isinstance(exc, YouTubeRequestFailed):
        return TranscriptError(
            "YouTube returned an error.", hint="Usually temporary — try again."
        )
    if isinstance(exc, CouldNotRetrieveTranscript):
        return TranscriptError(
            "Could not retrieve a transcript for this video.",
            hint=str(exc).splitlines()[0][:200] if str(exc) else "",
        )
    return TranscriptError(
        "Something went wrong talking to YouTube.", hint=f"{type(exc).__name__}: {exc}"[:200]
    )


def list_languages(video_id: str) -> list[dict]:
    """Every caption track available, manual ones first."""
    try:
        tracks = YouTubeTranscriptApi().list(video_id)
    except Exception as exc:
        raise _to_friendly(exc) from exc

    out = [
        {
            "code": t.language_code,
            "name": t.language,
            "generated": bool(t.is_generated),
        }
        for t in tracks
    ]
    out.sort(key=lambda t: (t["generated"], t["name"]))
    return out


def fetch(video_id: str, language: str | None = None, *, use_cache: bool = True) -> dict:
    """Fetch one transcript. Returns a JSON-ready dict."""
    path = _cache_path(video_id, language)
    if use_cache:
        cached = _read_cache(path)
        if cached:
            cached["cached"] = True
            return cached

    langs = [language] if language else ["en", "en-US", "en-GB"]
    try:
        api = YouTubeTranscriptApi()
        try:
            fetched = api.fetch(video_id, languages=langs)
        except NoTranscriptFound:
            if language:
                raise
            # No English track — fall back to whatever the video does have.
            tracks = api.list(video_id)
            first = next(iter(tracks), None)
            if first is None:
                raise
            fetched = first.fetch()
    except TranscriptError:
        raise
    except Exception as exc:
        raise _to_friendly(exc) from exc

    segments = [
        Segment(text=s.text, start=float(s.start), duration=float(s.duration))
        for s in fetched.snippets
    ]

    payload = {
        "video_id": video_id,
        "language": fetched.language,
        "language_code": fetched.language_code,
        "generated": bool(fetched.is_generated),
        "segments": [asdict(s) for s in segments],
        "text_plain": to_plain_text(segments),
        "text_timestamped": to_timestamped_text(segments),
        "duration": (segments[-1].start + segments[-1].duration) if segments else 0.0,
        "word_count": len(to_plain_text(segments).split()),
        "cached": False,
    }
    _write_cache(path, payload)
    return payload
