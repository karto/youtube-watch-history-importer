import json
from datetime import datetime
from pathlib import Path

from youtube_watch_history_importer.cli import (
    Config,
    DailySummary,
    ProgressReporter,
    VideoMetadata,
    VideoSummary,
    WatchEntry,
    archive_attachment_dir,
    archive_note_path,
    canonical_watch_url,
    find_existing_archive_note,
    decode_video_cache,
    daily_note_path,
    ensure_daily_note_frontmatter,
    fallback_video_metadata,
    fetch_transcript_from_info,
    format_video_cache_comment,
    load_daily_notes_config,
    normalize_tags,
    obsidian_format_to_strftime,
    parse_args,
    process_video,
    rebuild_day_summary_from_cache,
    render_archive_note,
    render_daily_block,
    render_embed_player,
    subtitle_language_rank,
    transcript_from_json3,
    transcript_from_text,
    upsert_managed_section,
)


def sample_entry() -> WatchEntry:
    return WatchEntry(
        video_id="abc123",
        watched_at=datetime.fromisoformat("2026-03-06T10:15:00+01:00"),
        title="Video Title",
        url="https://www.youtube.com/watch?v=abc123",
        channel="Channel Name",
    )


def sample_meta() -> VideoMetadata:
    return VideoMetadata(
        video_id="abc123",
        title="Video Title",
        url="https://www.youtube.com/watch?v=abc123",
        description="Long description here.",
        channel="Channel Name",
        channel_url="https://www.youtube.com/@channel",
        thumbnail_url="https://img.youtube.com/vi/abc123/maxresdefault.webp",
        comments=[{"author": "alice", "like_count": 2, "text": "Great video"}],
        transcript="Hello world transcript.",
        upload_date="20260301",
        duration=123,
        view_count=456,
        like_count=789,
        webpage_url="https://www.youtube.com/watch?v=abc123",
        extractor="Youtube",
    )


def sample_summary() -> VideoSummary:
    return VideoSummary(teaser="Short teaser.", summary="Para 1.\n\nPara 2.\n\nPara 3.", tags=["tag1", "tag2"])


def test_obsidian_format_to_strftime():
    assert obsidian_format_to_strftime("YYYY/MM/YYYY-MM-DD") == "%Y/%m/%Y-%m-%d"


def test_load_daily_notes_config(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "daily-notes.json").write_text(json.dumps({"folder": "Daily Notes", "format": "YYYY/MM/YYYY-MM-DD"}))
    assert load_daily_notes_config(tmp_path) == {"folder": "Daily Notes", "format": "%Y/%m/%Y-%m-%d"}


def test_parse_args_defaults_force_rebuild_and_verbosity_levels(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "daily-notes.json").write_text(json.dumps({"folder": "Daily Notes", "format": "YYYY/MM/YYYY-MM-DD"}))
    config = parse_args(["archive.tgz", str(tmp_path), "--force", "--rebuild-from-cache", "-vv", "--progress"])
    assert config.llm_provider == "ollama"
    assert config.llm_model == "Qwen3.5:4B"
    assert config.notes_root == tmp_path / "Daily Notes"
    assert config.force is True
    assert config.rebuild_from_cache is True
    assert config.resummarize_from_cache is False
    assert config.verbose == 2
    assert config.progress is True


def test_parse_args_openai_provider_uses_gpt_4o_mini(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "daily-notes.json").write_text(json.dumps({"folder": "Daily Notes", "format": "YYYY/MM/YYYY-MM-DD"}))
    config = parse_args(["archive.tgz", str(tmp_path), "--llm-provider", "openai"])
    assert config.llm_provider == "openai"
    assert config.llm_model == "gpt-4o-mini"


def test_parse_args_resummarize_from_cache(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "daily-notes.json").write_text(json.dumps({"folder": "Daily Notes", "format": "YYYY/MM/YYYY-MM-DD"}))
    config = parse_args(["archive.tgz", str(tmp_path), "--resummarize-from-cache"])
    assert config.resummarize_from_cache is True


def test_archive_paths():
    entry = sample_entry()
    meta = sample_meta()
    vault = Path("/vault")
    assert archive_attachment_dir(vault, entry) == Path("/vault/Social Archives/attachments/youtube/2026/abc123")
    assert archive_note_path(vault, entry, meta) == Path("/vault/Social Archives/Youtube/2026/03/2026-03-06 - Channel Name - Video Title - (abc123).md")


def test_canonical_watch_url_rewrites_music_youtube():
    assert canonical_watch_url("https://music.youtube.com/watch?v=abc123", "abc123") == "https://www.youtube.com/watch?v=abc123"
    assert canonical_watch_url("https://www.youtube.com/watch?v=abc123", "abc123") == "https://www.youtube.com/watch?v=abc123"


def test_fallback_video_metadata_uses_minimal_safe_defaults():
    entry = WatchEntry(
        video_id="abc123",
        watched_at=datetime.fromisoformat("2026-03-06T10:15:00+01:00"),
        title="Video Title",
        url="https://music.youtube.com/watch?v=abc123",
        channel="Channel Name",
    )
    meta = fallback_video_metadata(entry)
    assert meta.video_id == "abc123"
    assert meta.title == "Video Title"
    assert meta.url == "https://www.youtube.com/watch?v=abc123"
    assert meta.webpage_url == "https://www.youtube.com/watch?v=abc123"
    assert meta.comments == []
    assert meta.transcript == ""


def test_find_existing_archive_note_finds_by_video_id_when_filename_differs(tmp_path):
    entry = WatchEntry(
        video_id="QCgeitX5xg0",
        watched_at=datetime.fromisoformat("2024-10-02T10:15:00+01:00"),
        title="Lovely Day",
        url="https://www.youtube.com/watch?v=QCgeitX5xg0",
        channel="Bill Withers - Topic",
    )
    existing = tmp_path / "Social Archives" / "Youtube" / "2024" / "10" / "2024-10-02 - Bill Withers - Lovely Day - (QCgeitX5xg0).md"
    existing.parent.mkdir(parents=True)
    existing.write_text("cached")
    found = find_existing_archive_note(tmp_path, entry)
    assert found == existing


def test_daily_note_path():
    assert daily_note_path(Path("/notes"), "2026-03-06", "%Y/%m/%Y-%m-%d") == Path("/notes/2026/03/2026-03-06.md")


def test_render_embed_player():
    assert "youtube.com/embed/abc123" in render_embed_player("abc123")


def test_transcript_parsers():
    json3 = json.dumps({"events": [{"segs": [{"utf8": "Hello "}, {"utf8": "world"}]}]})
    assert transcript_from_json3(json3) == "Hello world"
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello <b>world</b>\n"
    assert transcript_from_text(text) == "Hello world"


def test_subtitle_language_rank_prefers_english_then_danish():
    assert subtitle_language_rank("en") == 0
    assert subtitle_language_rank("en-US") == 0
    assert subtitle_language_rank("da") == 1
    assert subtitle_language_rank("da-DK") == 1
    assert subtitle_language_rank("de") is None


def test_normalize_tags_handles_strings_and_split_letters():
    assert normalize_tags(["music", "classic", "soul"]) == ["music", "classic", "soul"]
    assert normalize_tags("#music #classic #soul") == ["music", "classic", "soul"]
    assert normalize_tags(list("music #classic #soul #positivity #nostalgia")) == ["music", "classic", "soul", "positivity", "nostalgia"]


def test_fetch_transcript_from_info_prefers_english_then_danish(monkeypatch):
    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def read(self):
            return self.text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(url, timeout=120):
        if url == "https://example.com/en.vtt":
            return DummyResponse("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nEnglish transcript\n")
        if url == "https://example.com/da.vtt":
            return DummyResponse("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nDansk transskript\n")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    info = {
        "subtitles": {
            "de": [{"url": "https://example.com/de.vtt", "ext": "vtt"}],
            "da": [{"url": "https://example.com/da.vtt", "ext": "vtt"}],
            "en": [{"url": "https://example.com/en.vtt", "ext": "vtt"}],
        }
    }
    assert fetch_transcript_from_info(info) == "English transcript"


def test_fetch_transcript_from_info_falls_back_to_danish(monkeypatch):
    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def read(self):
            return self.text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(url, timeout=120):
        if url == "https://example.com/da.vtt":
            return DummyResponse("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nDansk transskript\n")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    info = {
        "automatic_captions": {
            "fr": [{"url": "https://example.com/fr.vtt", "ext": "vtt"}],
            "da": [{"url": "https://example.com/da.vtt", "ext": "vtt"}],
        }
    }
    assert fetch_transcript_from_info(info) == "Dansk transskript"


def test_format_video_cache_comment_wraps_to_max_180_chars():
    comment = format_video_cache_comment("a" * 500, max_len=180)
    for line in comment.splitlines():
        assert len(line) <= 180


def test_render_archive_note_and_cache_roundtrip():
    vault = Path("/vault")
    note = archive_note_path(vault, sample_entry(), sample_meta())
    thumb = Path("/vault/Social Archives/attachments/youtube/2026/abc123/thumb.webp")
    content = render_archive_note(vault, note, sample_entry(), sample_meta(), sample_summary(), thumb)
    assert "platform: youtube" in content
    assert 'teaser: "Short teaser."' in content
    assert "## Summary" in content
    assert "## Transcript" in content
    assert "## Short Summary" not in content
    assert "youtube.com/embed/abc123" in content
    assert "<!-- ywhi-video-cache:" in content
    for line in content.splitlines():
        if "ywhi-video-cache" in line or line.startswith("ey") or line.endswith("-->"):
            assert len(line) <= 180
    cached = decode_video_cache(content, note, vault)
    assert cached is not None
    entry, meta, summary, thumb_path = cached
    assert entry.video_id == "abc123"
    assert meta.transcript == "Hello world transcript."
    assert summary.tags == ["tag1", "tag2"]
    assert thumb_path == thumb


def test_render_daily_block_and_upsert():
    block = render_daily_block(DailySummary(summary="Para1\n\nPara2\n\nPara3", tags=["one", "two"]))
    assert block.startswith("## YouTube Watch History")
    assert "#one #two" in block
    updated = upsert_managed_section("# Note\n\nBody\n", "## YouTube Watch History", block)
    assert "cssclasses: [cards,cards-16-9,cards-cover,table-max]" in updated
    assert updated.endswith(block)


def test_ensure_daily_note_frontmatter_adds_or_replaces_cssclasses():
    inserted = ensure_daily_note_frontmatter("# Note\n")
    assert inserted.startswith("---\ncssclasses: [cards,cards-16-9,cards-cover,table-max]\n---\n\n")

    existing = "---\naliases: [x]\ncssclasses: [old]\n---\n\n# Note\n"
    updated = ensure_daily_note_frontmatter(existing)
    assert "aliases: [x]" in updated
    assert "cssclasses: [cards,cards-16-9,cards-cover,table-max]" in updated
    assert "cssclasses: [old]" not in updated


def test_rebuild_day_summary_from_cache_uses_long_summaries_and_tags():
    items = [(sample_entry(), sample_meta(), sample_summary())]
    summary = rebuild_day_summary_from_cache(items)
    assert "Para 1." in summary.summary
    assert summary.tags == ["tag1", "tag2"]


def test_process_video_rebuild_from_cache_skips_when_note_missing(tmp_path):
    config = Config(
        takeout_archive=tmp_path / "archive.tgz",
        vault_root=tmp_path,
        notes_root=tmp_path / "Daily Notes",
        note_format="%Y/%m/%Y-%m-%d",
        llm_provider="ollama",
        llm_model="Qwen3.5:4B",
        heading="## YouTube Watch History",
        dry_run=False,
        verbose=0,
        progress=False,
        force=False,
        rebuild_from_cache=True,
        resummarize_from_cache=False,
        max_comments=20,
        start_date=None,
        end_date=None,
        limit_days=None,
        timezone=None,
    )
    reporter = ProgressReporter(verbosity=0, progress=False)
    result = process_video(sample_entry(), config, reporter)
    assert result is None


def test_process_video_resummarize_from_cache_rewrites_note_without_fetch(tmp_path, monkeypatch):
    entry = sample_entry()
    meta = sample_meta()
    old_summary = sample_summary()
    note = archive_note_path(tmp_path, entry, meta)
    thumb = tmp_path / "Social Archives/attachments/youtube/2026/abc123/thumb.webp"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(render_archive_note(tmp_path, note, entry, meta, old_summary, thumb))

    def fake_summarize_video(entry_arg, meta_arg, model_arg):
        return VideoSummary(teaser="Fresh teaser.", summary="Fresh summary.", tags=["fresh"]), 123

    def fail_fetch(*args, **kwargs):
        raise AssertionError("fetch_video_metadata should not be called")

    monkeypatch.setattr("youtube_watch_history_importer.cli.summarize_video", fake_summarize_video)
    monkeypatch.setattr("youtube_watch_history_importer.cli.fetch_video_metadata", fail_fetch)

    config = Config(
        takeout_archive=tmp_path / "archive.tgz",
        vault_root=tmp_path,
        notes_root=tmp_path / "Daily Notes",
        note_format="%Y/%m/%Y-%m-%d",
        llm_provider="ollama",
        llm_model="Qwen3.5:4B",
        heading="## YouTube Watch History",
        dry_run=False,
        verbose=0,
        progress=False,
        force=False,
        rebuild_from_cache=False,
        resummarize_from_cache=True,
        max_comments=20,
        start_date=None,
        end_date=None,
        limit_days=None,
        timezone=None,
    )
    reporter = ProgressReporter(verbosity=0, progress=False)
    result = process_video(entry, config, reporter)
    assert result is not None
    rewritten = note.read_text()
    assert 'teaser: "Fresh teaser."' in rewritten
    assert "Fresh summary." in rewritten

