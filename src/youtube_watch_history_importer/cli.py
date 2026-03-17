from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

HEADING = "## YouTube Watch History"
DAILY_NOTE_CSSCLASSES = "[cards,cards-16-9,cards-cover,table-max]"
DEFAULT_OLLAMA_MODEL = "Qwen3.5:4B"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
MAX_DESCRIPTION = 1200
MAX_COMMENT_LENGTH = 280
MAX_COMMENTS = 20
ARCHIVE_FOLDER = Path("Social Archives/Youtube")
ATTACHMENTS_FOLDER = Path("Social Archives/attachments/youtube")
T = TypeVar("T")


def payload_context_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


@dataclass
class WatchEntry:
    video_id: str
    watched_at: datetime
    title: str
    url: str
    channel: str | None = None


@dataclass
class VideoMetadata:
    video_id: str
    title: str
    url: str
    description: str
    channel: str | None
    channel_url: str | None
    thumbnail_url: str | None
    comments: list[dict[str, Any]]
    transcript: str
    upload_date: str | None
    duration: int | None
    view_count: int | None
    like_count: int | None
    webpage_url: str | None
    extractor: str | None


@dataclass
class VideoSummary:
    teaser: str
    summary: str
    tags: list[str]


@dataclass
class DailySummary:
    summary: str
    tags: list[str]


@dataclass
class Config:
    takeout_archive: Path
    vault_root: Path
    notes_root: Path
    note_format: str
    llm_provider: str
    llm_model: str
    heading: str
    dry_run: bool
    verbose: int
    progress: bool
    force: bool
    rebuild_from_cache: bool
    resummarize_from_cache: bool
    max_comments: int
    start_date: str | None
    end_date: str | None
    limit_days: int | None
    timezone: str | None


class ProgressReporter:
    def __init__(self, verbosity: int, progress: bool):
        self.verbosity = verbosity
        self.progress_enabled = progress
        self.last_len = 0

    def log(self, message: str, level: int = 1) -> None:
        if self.verbosity < level:
            return
        self.clear()
        print(message, file=sys.stderr)

    def timed(self, label: str, started_at: float, level: int = 2) -> None:
        if self.verbosity < level:
            return
        elapsed = time.perf_counter() - started_at
        self.log(f"[timing] {label}: {elapsed:.2f}s", level=level)

    def bar(self, prefix: str, current: int, total: int) -> None:
        if not self.progress_enabled:
            return
        total = max(total, 1)
        width = 30
        filled = int(width * current / total)
        bar = "#" * filled + "-" * (width - filled)
        message = f"\r{prefix} [{bar}] {current}/{total}"
        print(message, end="", file=sys.stderr, flush=True)
        self.last_len = max(self.last_len, len(message))
        if current >= total:
            self.clear(final=True)

    def clear(self, final: bool = False) -> None:
        if self.last_len:
            print(
                "\r" + " " * self.last_len + "\r", end="", file=sys.stderr, flush=True
            )
            if final:
                print(file=sys.stderr)
            self.last_len = 0


def parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(
        description="Import YouTube watch history into Obsidian social archives"
    )
    parser.add_argument(
        "takeout_archive", type=Path, help="Path to Google Takeout .tgz archive"
    )
    parser.add_argument(
        "vault_root", type=Path, help="Root directory of the Obsidian vault"
    )
    parser.add_argument(
        "--notes-folder",
        default=None,
        help="Daily notes folder relative to vault root; overrides .obsidian/daily-notes.json",
    )
    parser.add_argument(
        "--note-format",
        default=None,
        help="strftime note format; overrides .obsidian/daily-notes.json",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["ollama", "openai"],
        default="ollama",
        help="LLM backend to use for summaries/tags (default: ollama)",
    )
    parser.add_argument(
        "--ollama-model",
        default=DEFAULT_OLLAMA_MODEL,
        help=f"Ollama model (default: {DEFAULT_OLLAMA_MODEL})",
    )
    parser.add_argument(
        "--openai-model",
        default=DEFAULT_OPENAI_MODEL,
        help=f"OpenAI model when --llm-provider openai (default: {DEFAULT_OPENAI_MODEL})",
    )
    parser.add_argument(
        "--heading", default=HEADING, help="Daily note heading to manage"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Use -v for per-video logs, -vv to include step timings",
    )
    parser.add_argument("--progress", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cached archive notes and refetch/re-summarize",
    )
    parser.add_argument(
        "--rebuild-from-cache",
        action="store_true",
        help="Re-render archive notes and daily rollups using embedded cache only; no external fetches or LLM calls",
    )
    parser.add_argument(
        "--resummarize-from-cache",
        action="store_true",
        help="Regenerate teaser/summary/tags and daily rollups from embedded cache only; no yt-dlp or other metadata fetches",
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        default=20,
        help="Max top comments to include per video (default: 20)",
    )
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--limit-days", type=int)
    parser.add_argument("--timezone")
    args = parser.parse_args(argv)

    vault_root = args.vault_root.expanduser().resolve()
    daily_notes_config = load_daily_notes_config(vault_root)
    notes_folder = args.notes_folder or daily_notes_config["folder"]
    note_format = args.note_format or daily_notes_config["format"]
    llm_model = (
        args.openai_model if args.llm_provider == "openai" else args.ollama_model
    )
    return Config(
        takeout_archive=args.takeout_archive.expanduser().resolve(),
        vault_root=vault_root,
        notes_root=vault_root / notes_folder,
        note_format=note_format,
        llm_provider=args.llm_provider,
        llm_model=llm_model,
        heading=args.heading,
        dry_run=args.dry_run,
        verbose=args.verbose,
        progress=args.progress,
        force=args.force,
        rebuild_from_cache=args.rebuild_from_cache,
        resummarize_from_cache=args.resummarize_from_cache,
        max_comments=max(0, min(args.max_comments, MAX_COMMENTS)),
        start_date=args.start_date,
        end_date=args.end_date,
        limit_days=args.limit_days,
        timezone=args.timezone,
    )


def load_daily_notes_config(vault_root: Path) -> dict[str, str]:
    config_path = vault_root / ".obsidian" / "daily-notes.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Obsidian daily notes config not found: {config_path}")
    raw = json.loads(config_path.read_text())
    folder = raw.get("folder")
    fmt = raw.get("format")
    if not folder or not fmt:
        raise ValueError(f"Invalid Obsidian daily notes config: {config_path}")
    return {
        "folder": str(folder).strip("/"),
        "format": obsidian_format_to_strftime(str(fmt)),
    }


def obsidian_format_to_strftime(fmt: str) -> str:
    replacements = [
        ("YYYY", "%Y"),
        ("YY", "%y"),
        ("MM", "%m"),
        ("DD", "%d"),
        ("HH", "%H"),
        ("mm", "%M"),
        ("ss", "%S"),
    ]
    result = fmt
    for source, target in replacements:
        result = result.replace(source, target)
    return result


def resolve_timezone(timezone_name: str | None):
    if timezone_name:
        from zoneinfo import ZoneInfo

        return ZoneInfo(timezone_name)
    return datetime.now().astimezone().tzinfo or UTC


def extract_watch_entries(
    archive_path: Path, timezone_name: str | None = None
) -> list[WatchEntry]:
    with tarfile.open(archive_path, "r:*") as tar:
        member = next(
            (m for m in tar.getmembers() if m.name.endswith("watch-history.json")), None
        )
        if member is None:
            raise FileNotFoundError("watch-history.json not found in archive")
        raw = json.load(tar.extractfile(member))
    entries: list[WatchEntry] = []
    tz = resolve_timezone(timezone_name)
    for item in raw:
        url = item.get("titleUrl")
        watched = item.get("time")
        title = item.get("title") or ""
        if not url or not watched or not title.startswith("Watched "):
            continue
        parsed_url = urllib.parse.urlparse(url)
        video_id = urllib.parse.parse_qs(parsed_url.query).get("v", [""])[0]
        if not video_id:
            continue
        subtitles = item.get("subtitles") or []
        channel = (
            subtitles[0].get("name")
            if subtitles and isinstance(subtitles, list)
            else None
        )
        entries.append(
            WatchEntry(
                video_id=video_id,
                watched_at=datetime.fromisoformat(
                    watched.replace("Z", "+00:00")
                ).astimezone(tz),
                title=title.removeprefix("Watched ").strip(),
                url=url,
                channel=channel,
            )
        )
    entries.sort(key=lambda x: x.watched_at)
    return entries


def filter_entries(
    entries: list[WatchEntry],
    start_date: str | None,
    end_date: str | None,
    limit_days: int | None,
) -> list[WatchEntry]:
    if start_date:
        entries = [e for e in entries if e.watched_at.date().isoformat() >= start_date]
    if end_date:
        entries = [e for e in entries if e.watched_at.date().isoformat() <= end_date]
    if limit_days:
        days = sorted({e.watched_at.date().isoformat() for e in entries}, reverse=True)[
            :limit_days
        ]
        entries = [e for e in entries if e.watched_at.date().isoformat() in set(days)]
    return entries


def group_by_day(entries: list[WatchEntry]) -> dict[str, list[WatchEntry]]:
    grouped: dict[str, list[WatchEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.watched_at.date().isoformat(), []).append(entry)
    return dict(sorted(grouped.items()))


def canonical_watch_url(url: str, video_id: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("music.youtube.com"):
        return f"https://www.youtube.com/watch?v={video_id}"
    return url


def fallback_video_metadata(entry: WatchEntry) -> VideoMetadata:
    canonical_url = canonical_watch_url(entry.url, entry.video_id)
    return VideoMetadata(
        video_id=entry.video_id,
        title=entry.title,
        url=canonical_url,
        description="",
        channel=entry.channel,
        channel_url=None,
        thumbnail_url=None,
        comments=[],
        transcript="",
        upload_date=None,
        duration=None,
        view_count=None,
        like_count=None,
        webpage_url=canonical_url,
        extractor=None,
    )


def fetch_video_metadata(
    entry: WatchEntry, max_comments: int
) -> tuple[VideoMetadata, float]:
    target_url = canonical_watch_url(entry.url, entry.video_id)
    command = [
        "yt-dlp",
        "-J",
        "--skip-download",
        "--no-playlist",
        "--no-warnings",
        "--write-comments",
        target_url,
    ]
    yt_dlp_started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    yt_dlp_elapsed = time.perf_counter() - yt_dlp_started
    info = json.loads(result.stdout)
    comments: list[dict[str, Any]] = []
    for comment in (info.get("comments") or [])[:max_comments]:
        comments.append(
            {
                "author": comment.get("author")
                or comment.get("author_id")
                or "unknown",
                "like_count": comment.get("like_count") or 0,
                "text": clean_text(comment.get("text") or ""),
            }
        )
    transcript = fetch_transcript_from_info(info)
    return (
        VideoMetadata(
            video_id=entry.video_id,
            title=clean_text(info.get("title") or entry.title),
            url=info.get("webpage_url") or entry.url,
            description=clean_text(info.get("description") or ""),
            channel=info.get("channel") or info.get("uploader") or entry.channel,
            channel_url=info.get("channel_url") or info.get("uploader_url"),
            thumbnail_url=best_thumbnail_url(info),
            comments=comments,
            transcript=transcript,
            upload_date=info.get("upload_date"),
            duration=info.get("duration"),
            view_count=info.get("view_count"),
            like_count=info.get("like_count"),
            webpage_url=info.get("webpage_url") or entry.url,
            extractor=info.get("extractor_key") or info.get("extractor"),
        ),
        yt_dlp_elapsed,
    )


def subtitle_language_rank(lang: str) -> int | None:
    normalized = (lang or "").lower()
    if normalized == "en" or normalized.startswith("en-"):
        return 0
    if normalized == "da" or normalized.startswith("da-"):
        return 1
    return None


def fetch_transcript_from_info(info: dict[str, Any]) -> str:
    sources: list[dict[str, Any]] = []
    for container in (
        info.get("subtitles") or {},
        info.get("automatic_captions") or {},
    ):
        if isinstance(container, dict):
            for lang, items in container.items():
                for item in items or []:
                    candidate = dict(item)
                    candidate["lang"] = lang
                    sources.append(candidate)

    def rank(item: dict[str, Any]) -> tuple[int, int]:
        lang = item.get("lang", "")
        ext = item.get("ext", "")
        lang_rank = subtitle_language_rank(lang)
        ext_rank = {"json3": 0, "srv3": 1, "vtt": 2, "ttml": 3}.get(ext, 9)
        return (lang_rank if lang_rank is not None else 99, ext_rank)

    preferred_sources = [
        s for s in sources if subtitle_language_rank(s.get("lang", "")) is not None
    ]
    for source in sorted(preferred_sources, key=rank):
        url = source.get("url")
        if not url:
            continue
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read().decode("utf-8", errors="ignore")
            ext = source.get("ext", "")
            if ext == "json3":
                return transcript_from_json3(data)
            return transcript_from_text(data)
        except Exception:
            continue
    return ""


def transcript_from_json3(data: str) -> str:
    raw = json.loads(data)
    chunks = []
    for event in raw.get("events", []):
        for seg in event.get("segs", []) or []:
            text = seg.get("utf8") or ""
            if text.strip():
                chunks.append(text)
    return clean_text("".join(chunks))


def transcript_from_text(data: str) -> str:
    lines = []
    for line in data.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("WEBVTT", "NOTE")):
            continue
        if re.match(r"^\d{2}:\d{2}", stripped) or re.match(r"^\d+$", stripped):
            continue
        stripped = re.sub(r"<[^>]+>", "", stripped)
        if stripped:
            lines.append(stripped)
    return clean_text("\n".join(lines))


THUMBNAIL_PREFERENCE = (
    "maxresdefault",
    "sddefault",
    "hqdefault",
    "mqdefault",
    "default",
)


def best_thumbnail_url(info: dict[str, Any]) -> str | None:
    thumbs = info.get("thumbnails") or []
    for preferred in THUMBNAIL_PREFERENCE:
        for thumb in reversed(thumbs):
            url = thumb.get("url")
            if url and preferred in url:
                return url
    return info.get("thumbnail")


def clean_text(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def single_line(text: str) -> str:
    return re.sub(r"\s+", " ", clean_text(text))


def truncate(text: str, limit: int) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def slug_tag(tag: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", tag.strip().lower()).strip("-")


def normalize_tags(raw_tags: Any) -> list[str]:
    if raw_tags is None:
        return []
    if isinstance(raw_tags, str):
        values = [part for part in re.split(r"[,#\n]+", raw_tags) if part.strip()]
    elif isinstance(raw_tags, list):
        if raw_tags and all(
            isinstance(tag, str) and len(tag.strip()) <= 1 for tag in raw_tags
        ):
            joined = "".join(str(tag) for tag in raw_tags)
            values = [part for part in re.split(r"[,#\n]+", joined) if part.strip()]
        else:
            values = []
            i = 0
            while i < len(raw_tags):
                item = raw_tags[i]
                if isinstance(item, str) and len(item.strip()) == 1:
                    chars: list[str] = []
                    while (
                        i < len(raw_tags)
                        and isinstance(raw_tags[i], str)
                        and len(raw_tags[i].strip()) == 1
                    ):
                        chars.append(str(raw_tags[i]))
                        i += 1
                    values.extend(
                        part
                        for part in re.split(r"[,#\n]+", "".join(chars))
                        if part.strip()
                    )
                    continue
                values.append(str(item))
                i += 1
    else:
        values = [str(raw_tags)]
    normalized: list[str] = []
    for value in values:
        tag = slug_tag(value.replace("#", " "))
        if tag and tag not in normalized:
            normalized.append(tag)
    return normalized


def archive_note_path(
    vault_root: Path, entry: WatchEntry, meta: VideoMetadata | None = None
) -> Path:
    date_dir = entry.watched_at.strftime("%Y/%m")
    title = sanitize_filename(meta.title if meta else entry.title, 80)
    channel = sanitize_filename(
        meta.channel if meta and meta.channel else entry.channel or "Unknown", 40
    )
    filename = f"{entry.watched_at.date().isoformat()} - {channel} - {title} - ({entry.video_id}).md"
    return vault_root / ARCHIVE_FOLDER / date_dir / filename


def find_existing_archive_note(vault_root: Path, entry: WatchEntry) -> Path | None:
    expected = archive_note_path(vault_root, entry)
    if expected.exists():
        return expected
    date_dir = vault_root / ARCHIVE_FOLDER / entry.watched_at.strftime("%Y/%m")
    if not date_dir.exists():
        return None
    matches = sorted(date_dir.glob(f"*({entry.video_id}).md"))
    return matches[0] if matches else None


def archive_attachment_dir(vault_root: Path, entry: WatchEntry) -> Path:
    return (
        vault_root
        / ATTACHMENTS_FOLDER
        / entry.watched_at.strftime("%Y")
        / entry.video_id
    )


def sanitize_filename(text: str, limit: int) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]", "-", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return truncate(cleaned, limit)


def download_thumbnail(
    url: str | None, attachment_dir: Path, video_id: str, dry_run: bool
) -> Path | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix or ".jpg"
    digest = hashlib.sha1(url.encode()).hexdigest()[:8]
    target = attachment_dir / f"{video_id}-{digest}{ext}"
    if dry_run:
        return target
    attachment_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    try:
        with urllib.request.urlopen(url) as response, target.open("wb") as fh:
            shutil.copyfileobj(response, fh)
    except urllib.error.URLError:
        return None
    return target


def summarize_video(
    entry: WatchEntry, meta: VideoMetadata, model: str
) -> tuple[VideoSummary, int]:
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "seed": 0},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Summarize a watched YouTube video for an Obsidian archive note. "
                    "Return JSON with keys teaser, summary, and tags. "
                    "teaser must be under 50 words and read like a teaser or hook, not a summary lead-in. "
                    "Do not start teaser with phrases like 'In this video', 'This video', or 'The video'. "
                    "Summary should be 3 paragraphs or less and only as much as relevant. "
                    "Do not start summary with phrases like 'In this video', 'This video', or 'The video'. "
                    "Use title, description, transcript, and comments to infer themes. tags should be short, hashtagless strings."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "watched_at": entry.watched_at.isoformat(),
                        "title": meta.title,
                        "channel": meta.channel,
                        "description": truncate(meta.description, 4000),
                        "transcript": truncate(meta.transcript, 12000),
                        "comments": [
                            truncate(c["text"], 300) for c in meta.comments[:20]
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    raw = call_llm(payload)
    parsed = json.loads(raw)
    teaser = clean_text(parsed.get("teaser") or parsed.get("short_summary") or "")
    if len(teaser.split()) > 50:
        teaser = " ".join(teaser.split()[:50])
    summary = VideoSummary(
        teaser=teaser,
        summary=clean_text(parsed.get("summary") or parsed.get("long_summary") or ""),
        tags=normalize_tags(parsed.get("tags")),
    )
    context_size = payload_context_size(payload)
    if not summary.teaser or not summary.summary:
        return fallback_video_summary(meta), context_size
    return summary, context_size


def summarize_day(
    date_key: str,
    items: list[tuple[WatchEntry, VideoMetadata, VideoSummary]],
    model: str,
) -> tuple[DailySummary, int]:
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "seed": 0},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Summarize a day of watched YouTube videos for an Obsidian daily note. "
                    "Return JSON with keys summary and tags. "
                    "Summary should be 3 paragraphs or less and only as much as relevant. "
                    "Use all available context including video titles, descriptions, transcripts, comments, and per-video summaries."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "date": date_key,
                        "videos": [
                            {
                                "video_id": entry.video_id,
                                "title": meta.title,
                                "channel": meta.channel,
                                "teaser": summary.teaser,
                                "summary": summary.summary,
                                "description": truncate(meta.description, 1200),
                                "transcript": truncate(meta.transcript, 3000),
                                "comments": [
                                    truncate(c["text"], 220) for c in meta.comments[:20]
                                ],
                                "tags": summary.tags,
                            }
                            for entry, meta, summary in items
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    raw = call_llm(payload)
    parsed = json.loads(raw)
    tags = normalize_tags(parsed.get("tags"))
    # union video tags into daily tags
    for _, _, summary in items:
        for tag in summary.tags:
            if tag and tag not in tags:
                tags.append(tag)
    summary = DailySummary(summary=clean_text(parsed.get("summary") or ""), tags=tags)
    context_size = payload_context_size(payload)
    if not summary.summary:
        return fallback_day_summary(items), context_size
    return summary, context_size


def call_llm(payload: dict[str, Any]) -> str:
    model = payload.get("model", "")
    if model.startswith("gpt-"):
        return call_openai(payload)
    return call_ollama(payload)


def call_ollama(payload: dict[str, Any]) -> str:
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        raw = json.loads(response.read().decode())
    return raw["message"]["content"]


def call_openai(payload: dict[str, Any]) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set")
    body = {
        "model": payload["model"],
        "response_format": {"type": "json_object"},
        "messages": payload["messages"],
        "temperature": payload.get("options", {}).get("temperature", 0),
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        raw = json.loads(response.read().decode())
    return raw["choices"][0]["message"]["content"]


def fallback_video_summary(meta: VideoMetadata) -> VideoSummary:
    base = (
        truncate(meta.transcript or meta.description or meta.title, 280)
        or f"Watched {meta.title}."
    )
    return VideoSummary(
        teaser=" ".join(base.split()[:50]),
        summary=base,
        tags=[slug_tag(meta.channel or "youtube")],
    )


def fallback_day_summary(
    items: list[tuple[WatchEntry, VideoMetadata, VideoSummary]],
) -> DailySummary:
    return DailySummary(
        summary=f"Watched {len(items)} YouTube videos this day.",
        tags=list(dict.fromkeys(tag for _, _, s in items for tag in s.tags if tag)),
    )


def rebuild_day_summary_from_cache(
    items: list[tuple[WatchEntry, VideoMetadata, VideoSummary]],
) -> DailySummary:
    tags = list(dict.fromkeys(tag for _, _, s in items for tag in s.tags if tag))
    paragraphs = [s.summary.strip() for _, _, s in items if s.summary.strip()]
    if not paragraphs:
        return fallback_day_summary(items)
    summary = "\n\n".join(paragraphs[:5])
    return DailySummary(summary=summary, tags=tags)


def relative_to_vault(path: Path, vault_root: Path) -> str:
    try:
        return path.relative_to(vault_root).as_posix()
    except ValueError:
        return path.as_posix()


def render_embed_player(video_id: str) -> str:
    safe_id = html.escape(video_id)
    return (
        '<div class="video-container">'
        "<iframe "
        f'src="https://www.youtube.com/embed/{safe_id}" '
        'title="YouTube video player" frameborder="0" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
        'referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>'
        "</div>"
    )


def yaml_quote(value: str | None) -> str:
    if value is None:
        return '""'
    return json.dumps(str(value), ensure_ascii=False)


def render_archive_note(
    vault_root: Path,
    note_path: Path,
    entry: WatchEntry,
    meta: VideoMetadata,
    summary: VideoSummary,
    thumbnail_path: Path | None,
) -> str:
    imported = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M")
    published = format_publish(meta.upload_date)
    tags_line = ", ".join([yaml_quote(tag) for tag in summary.tags])
    tags_hash_line = " ".join([f"#{tag}" for tag in summary.tags])
    thumbnail_rel = (
        relative_to_vault(thumbnail_path, vault_root) if thumbnail_path else ""
    )
    parts = [
        "---",
        "platform: youtube",
        f"title: {yaml_quote(meta.title)}",
        f"author: {yaml_quote(meta.channel)}",
        f"authorUrl: {yaml_quote(meta.channel_url)}",
        f"published: {yaml_quote(published)}",
        f"archived: {yaml_quote(entry.watched_at.strftime('%Y-%m-%dT%H:%M'))}",
        f"imported: {yaml_quote(imported)}",
        f"originalUrl: {yaml_quote(meta.webpage_url or entry.url)}",
        f"videoId: {yaml_quote(entry.video_id)}",
        f"durationSeconds: {yaml_quote(meta.duration)}",
        f"views: {yaml_quote(meta.view_count)}",
        f"likes: {yaml_quote(meta.like_count)}",
        f"extractor: {yaml_quote(meta.extractor)}",
        f"teaser: {yaml_quote(summary.teaser)}",
        f"tags: [{tags_line}]",
        f"thumbnail: {yaml_quote('[[' + thumbnail_rel + ']]')}",
        "---",
        "",
        "## Summary",
        "",
        summary.summary,
        "",
    ]
    if meta.description:
        parts += ["## Description", "", meta.description, ""]
    parts += [
        "## Metadata",
        "",
        f"**Platform:** YouTube | **Channel:** [{meta.channel or 'Unknown'}]({meta.channel_url or ''}) | **Watched:** {entry.watched_at.strftime('%Y-%m-%d %H:%M')} | **Original URL:** {meta.webpage_url or entry.url}",
        "",
        f"Tags: {tags_hash_line}",
        "",
        "## Media",
        "",
    ]
    if thumbnail_rel:
        parts += [f"![[{thumbnail_rel}]]", ""]
    parts += [
        render_embed_player(entry.video_id),
        f"[Watch on YouTube]({meta.webpage_url or entry.url})",
        "",
    ]
    if meta.transcript:
        parts += ["## Transcript", "", meta.transcript, ""]
    parts += [
        "",
        format_video_cache_comment(
            encode_video_cache(
                entry, meta, summary, thumbnail_path, note_path, vault_root
            )
        ),
    ]
    return "\n".join(parts).rstrip() + "\n"


def format_publish(upload_date: str | None) -> str | None:
    if not upload_date or len(upload_date) != 8:
        return upload_date
    return f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"


def encode_video_cache(
    entry: WatchEntry,
    meta: VideoMetadata,
    summary: VideoSummary,
    thumbnail_path: Path | None,
    note_path: Path,
    vault_root: Path,
) -> str:
    payload = {
        "entry": {
            "video_id": entry.video_id,
            "watched_at": entry.watched_at.isoformat(),
            "title": entry.title,
            "url": entry.url,
            "channel": entry.channel,
        },
        "meta": asdict(meta),
        "summary": asdict(summary),
        "thumbnail_rel": relative_to_vault(thumbnail_path, vault_root)
        if thumbnail_path
        else None,
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False).encode()
    ).decode()


def format_video_cache_comment(encoded: str, max_len: int = 180) -> str:
    prefix = '<div class="hidden-cache"><!-- ywhi-video-cache:'
    suffix = " --></div>"
    first_width = max_len - len(prefix)
    next_width = max_len
    chunks = [encoded[:first_width]]
    remaining = encoded[first_width:]
    while remaining:
        chunks.append(remaining[:next_width])
        remaining = remaining[next_width:]
    lines = [prefix + chunks[0]]
    lines.extend(chunks[1:])
    lines[-1] = lines[-1] + suffix
    return "\n".join(lines)


def decode_video_cache(
    content: str, note_path: Path, vault_root: Path
) -> tuple[WatchEntry, VideoMetadata, VideoSummary, Path | None] | None:
    match = re.search(r"<!-- ywhi-video-cache:(.*?) -->", content, re.S)
    if not match:
        return None
    encoded = "".join(line.strip() for line in match.group(1).splitlines())
    raw = json.loads(base64.urlsafe_b64decode(encoded.encode()).decode())
    entry = WatchEntry(
        **{
            **raw["entry"],
            "watched_at": datetime.fromisoformat(raw["entry"]["watched_at"]),
        }
    )
    meta = VideoMetadata(**raw["meta"])
    summary_raw = dict(raw["summary"])
    if "teaser" not in summary_raw and "short_summary" in summary_raw:
        summary_raw["teaser"] = summary_raw.pop("short_summary")
    if "summary" not in summary_raw and "long_summary" in summary_raw:
        summary_raw["summary"] = summary_raw.pop("long_summary")
    summary = VideoSummary(**summary_raw)
    thumb_rel = raw.get("thumbnail_rel")
    thumb = vault_root / thumb_rel if thumb_rel else None
    return entry, meta, summary, thumb


def daily_note_path(notes_root: Path, date_key: str, note_format: str) -> Path:
    date_obj = datetime.strptime(date_key, "%Y-%m-%d")
    return notes_root / f"{date_obj.strftime(note_format)}.md"


def ensure_daily_note_frontmatter(existing: str) -> str:
    css_line = f"cssclasses: {DAILY_NOTE_CSSCLASSES}"
    if existing.startswith("---\n"):
        match = re.match(r"(?s)^(---\n)(.*?)(\n---\n?)", existing)
        if not match:
            return existing
        start, body, end = match.groups()
        if re.search(r"(?m)^cssclasses:\s*", body):
            body = re.sub(r"(?m)^cssclasses:\s*.*$", css_line, body)
        else:
            body = body.rstrip("\n")
            body = f"{body}\n{css_line}" if body else css_line
        return f"{start}{body}{end}{existing[match.end() :]}"
    return f"---\n{css_line}\n---\n\n{existing.lstrip()}"


def upsert_managed_section(existing: str, heading: str, block: str) -> str:
    existing = ensure_daily_note_frontmatter(existing)
    if not existing.strip():
        return block
    pattern = re.compile(rf"(?ms)^({re.escape(heading)}\s*\n.*?)(?=^##\s|\Z)")
    if pattern.search(existing):
        updated = pattern.sub(block.rstrip() + "\n\n", existing, count=1)
        return updated.rstrip() + "\n"
    return existing.rstrip() + "\n\n" + block


def render_daily_block(summary: DailySummary) -> str:
    lines = [HEADING, "", summary.summary, ""]
    if summary.tags:
        lines += [" ".join(f"#{tag}" for tag in summary.tags if tag), ""]
    lines += [
        "### Videos",
        """```dataview
TABLE WITHOUT ID embed(thumbnail) AS thumbnail, elink(originalUrl, title) as title, teaser, elink(authorUrl, author) as author
FROM "Social Archives/Youtube"
WHERE archived AND striptime(archived) = this.file.day
SORT archived ASC
```""",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_file(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def process_video(
    entry: WatchEntry, config: Config, reporter: ProgressReporter
) -> tuple[WatchEntry, VideoMetadata, VideoSummary, Path | None, Path] | None:
    overall_started = time.perf_counter()
    note_path = find_existing_archive_note(
        config.vault_root, entry
    ) or archive_note_path(config.vault_root, entry)
    cache_started = time.perf_counter()
    cached = None
    if note_path.exists():
        cached = decode_video_cache(note_path.read_text(), note_path, config.vault_root)
    reporter.timed(f"cache read {entry.video_id}", cache_started)

    if config.rebuild_from_cache or config.resummarize_from_cache:
        mode_name = (
            "rebuild-from-cache"
            if config.rebuild_from_cache
            else "resummarize-from-cache"
        )
        if not cached:
            reporter.log(
                f"[warn] {mode_name} skipped {entry.video_id}; no cache at {note_path}",
                level=1,
            )
            reporter.timed(
                f"video total {entry.video_id} (cache missing skip)", overall_started
            )
            return None
        cached_entry, meta, summary, thumb = cached
        if config.resummarize_from_cache:
            summary_started = time.perf_counter()
            try:
                summary, context_size = summarize_video(
                    cached_entry, meta, config.llm_model
                )
                reporter.log(
                    f"[context] video {entry.video_id}: {context_size} bytes sent to model",
                    level=2,
                )
            except Exception:
                summary = fallback_video_summary(meta)
            reporter.timed(
                f"resummarize video {entry.video_id} from cache", summary_started
            )
        rebuilt_path = archive_note_path(config.vault_root, cached_entry, meta)
        render_started = time.perf_counter()
        content = render_archive_note(
            config.vault_root, rebuilt_path, cached_entry, meta, summary, thumb
        )
        write_file(rebuilt_path, content, config.dry_run)
        reporter.timed(
            f"rewrite archive note from cache {entry.video_id}", render_started
        )
        reporter.timed(f"video total {entry.video_id} (cache mode)", overall_started)
        return cached_entry, meta, summary, thumb, rebuilt_path

    if cached and not config.force:
        cached_entry, meta, summary, thumb = cached
        reporter.timed(f"video total {entry.video_id} (cache hit)", overall_started)
        return cached_entry, meta, summary, thumb, note_path
    fetch_started = time.perf_counter()
    try:
        meta, yt_dlp_elapsed = fetch_video_metadata(entry, config.max_comments)
        reporter.log(
            f"[timing] yt-dlp {entry.video_id}: {yt_dlp_elapsed:.2f}s", level=2
        )
    except subprocess.CalledProcessError as exc:
        reporter.log(
            f"[warn] yt-dlp failed for {entry.video_id}; using fallback metadata ({exc.returncode})",
            level=1,
        )
        if exc.stderr:
            reporter.log(
                f"[warn] yt-dlp stderr {entry.video_id}: {single_line(exc.stderr)}",
                level=2,
            )
        meta = fallback_video_metadata(entry)
    reporter.timed(f"fetch metadata {entry.video_id}", fetch_started)
    attachment_dir = archive_attachment_dir(config.vault_root, entry)
    thumb_started = time.perf_counter()
    thumb = download_thumbnail(
        meta.thumbnail_url, attachment_dir, meta.video_id, config.dry_run
    )
    reporter.timed(f"download thumbnail {entry.video_id}", thumb_started)
    summary_started = time.perf_counter()
    try:
        summary, context_size = summarize_video(entry, meta, config.llm_model)
        reporter.log(
            f"[context] video {entry.video_id}: {context_size} bytes sent to model",
            level=2,
        )
    except Exception:
        summary = fallback_video_summary(meta)
    reporter.timed(f"summarize video {entry.video_id}", summary_started)
    note_path = archive_note_path(config.vault_root, entry, meta)
    render_started = time.perf_counter()
    content = render_archive_note(
        config.vault_root, note_path, entry, meta, summary, thumb
    )
    write_file(note_path, content, config.dry_run)
    reporter.timed(f"render+write archive note {entry.video_id}", render_started)
    reporter.timed(f"video total {entry.video_id}", overall_started)
    return entry, meta, summary, thumb, note_path


def main(argv: list[str] | None = None) -> int:
    total_started = time.perf_counter()
    config = parse_args(argv)
    reporter = ProgressReporter(config.verbose, config.progress)
    reporter.log(f"Reading archive: {config.takeout_archive}")
    read_started = time.perf_counter()
    entries = extract_watch_entries(config.takeout_archive, config.timezone)
    reporter.timed("read archive", read_started)
    filter_started = time.perf_counter()
    entries = filter_entries(
        entries, config.start_date, config.end_date, config.limit_days
    )
    grouped = group_by_day(entries)
    reporter.timed("filter+group entries", filter_started)
    if not grouped:
        print("No matching watch history entries found")
        return 0
    for day_index, (date_key, day_entries) in enumerate(grouped.items(), start=1):
        day_started = time.perf_counter()
        reporter.log(f"Processing day {day_index}/{len(grouped)}: {date_key}")
        processed: list[
            tuple[WatchEntry, VideoMetadata, VideoSummary, Path | None, Path]
        ] = []
        for video_index, entry in enumerate(day_entries, start=1):
            if config.verbose >= 1:
                reporter.log(
                    f"[{date_key} {video_index}/{len(day_entries)}] {entry.video_id} {entry.title}"
                )
            if config.progress:
                reporter.bar(date_key, video_index, len(day_entries))
            result = process_video(entry, config, reporter)
            if result is not None:
                processed.append(result)
        if not processed:
            reporter.log(
                f"[warn] no processed videos for {date_key}; skipping daily note",
                level=1,
            )
            reporter.timed(f"day total {date_key} (skipped)", day_started)
            continue
        items_for_day = [
            (entry, meta, summary) for entry, meta, summary, _, _ in processed
        ]
        day_summary_started = time.perf_counter()
        if config.rebuild_from_cache:
            day_summary = rebuild_day_summary_from_cache(items_for_day)
        else:
            try:
                day_summary, context_size = summarize_day(
                    date_key, items_for_day, config.llm_model
                )
                reporter.log(
                    f"[context] day {date_key}: {context_size} bytes sent to model",
                    level=2,
                )
            except Exception:
                day_summary = fallback_day_summary(items_for_day)
        reporter.timed(f"summarize day {date_key}", day_summary_started)
        daily_path = daily_note_path(config.notes_root, date_key, config.note_format)
        write_started = time.perf_counter()
        existing = daily_path.read_text() if daily_path.exists() else ""
        updated = upsert_managed_section(
            existing, config.heading, render_daily_block(day_summary)
        )
        write_file(daily_path, updated, config.dry_run)
        reporter.timed(f"write daily note {date_key}", write_started)
        if config.dry_run:
            print(f"=== DAILY {date_key} -> {daily_path} ===")
            print(render_daily_block(day_summary))
            for _, _, _, _, archive_path in processed:
                print(f"ARCHIVE {archive_path}")
        else:
            print(f"Updated {daily_path}")
        reporter.timed(f"day total {date_key}", day_started)
    reporter.timed("import total", total_started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
