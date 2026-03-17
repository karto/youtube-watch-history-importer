# youtube-watch-history-importer

Import YouTube watch history from a Google Takeout archive into Obsidian social archive notes and daily-note summaries.

## Features
- read `watch-history.json` directly from a `.tgz` takeout archive
- second positional argument is the Obsidian vault root
- daily notes folder and format are read from `.obsidian/daily-notes.json`
- create one archive note per watched video under `Social Archives/Youtube/YYYY/MM/`
- store assets under `Social Archives/attachments/youtube/<year>/<video-id>/`
- fetch video metadata with `yt-dlp`
- fetch top comments with `yt-dlp --write-comments`
- fetch subtitles/transcripts when available and include them inline in the note
- generate per-video teaser text, summaries, and tags via local Ollama/OpenAI
- generate per-day daily-note summaries plus aggregated tags
- `-v/--verbose` logs each video as it is processed
- `-vv` adds timing for each major step
- `--progress` shows a progress bar during import
- resume from cached archive notes is the default behavior
- `--force` ignores cached archive notes and reprocesses all videos
- `--rebuild-from-cache` rewrites archive notes and daily rollups from embedded cache only, with no external calls
- `--resummarize-from-cache` regenerates teaser/summary/tags and daily rollups from embedded cache only, with no `yt-dlp` calls

## Usage

Recommended install during development:

```bash
python3 -m pip install -e .
```

Then run either the console script or the module:

```bash
youtube-watch-history-importer \
  /path/to/takeout.tgz \
  ~/Obsidian/Personal \
  --start-date 2026-03-06 \
  --end-date 2026-03-06 \
  -vv \
  --progress \
  --dry-run
```

Or without installing, use `PYTHONPATH=src`:

```bash
PYTHONPATH=src python3 -m youtube_watch_history_importer \
  /path/to/takeout.tgz \
  ~/Obsidian/Personal \
  --start-date 2026-03-06 \
  --end-date 2026-03-06 \
  -vv \
  --progress \
  --dry-run
```

Rebuild note formatting from cached archive data only:

```bash
PYTHONPATH=src python3 -m youtube_watch_history_importer \
  /path/to/takeout.tgz \
  ~/Obsidian/Personal \
  --start-date 2026-03-06 \
  --end-date 2026-03-06 \
  --rebuild-from-cache
```

Regenerate teaser/summary/tags from cached archive data only:

```bash
PYTHONPATH=src python3 -m youtube_watch_history_importer \
  /path/to/takeout.tgz \
  ~/Obsidian/Personal \
  --start-date 2026-03-06 \
  --end-date 2026-03-06 \
  --resummarize-from-cache
```

Use OpenAI instead of Ollama:

```bash
export OPENAI_API_KEY=your_key_here
PYTHONPATH=src python3 -m youtube_watch_history_importer \
  /path/to/takeout.tgz \
  ~/Obsidian/Personal \
  --llm-provider openai \
  --start-date 2026-03-06 \
  --end-date 2026-03-06
```

## Notes
- daily notes folder and format come from `VAULT_ROOT/.obsidian/daily-notes.json`
- default LLM provider: `ollama` with model `Qwen3.5:4B`
- optional OpenAI provider: `--llm-provider openai` using model `gpt-4o-mini` by default and `OPENAI_API_KEY` from the environment
- per-video note filename format: `YYYY-MM-DD - Channel - Title - (video-id).md`
- daily notes get a bottom `## YouTube Watch History` section; reruns replace its contents
- short video summaries target fewer than 50 words
- long video summaries target 3-5 paragraphs when relevant
- daily summaries target 3-5 paragraphs and aggregate all video tags
