# Plan

## Context
- Change the importer from writing per-video entries into daily notes to creating individual archive notes under `Social Archives/YouTube` inside the Obsidian vault.
- Match the existing archive pattern already used for Instagram in `tmp/obsvlt/Social Archives/Instagram/...`.
- For each watched video, capture as much metadata as is practically available, download subtitles when available, and use those subtitles as input to summary generation.
- Daily notes should still receive a per-day rollup summary of everything watched that day, but no longer hold the full per-video archive payload.

## Approach
- Recommend keeping the Python CLI and extending it to produce two outputs:
  1. one archive note per watched YouTube item under `Social Archives/YouTube/YYYY/MM/...md`
  2. one `## YouTube Watch History` section in the Obsidian daily note for each watched day, appended at the bottom if missing and replaced if present.
- Reuse the current takeout parsing, Obsidian config resolution, and local Ollama summarization path.
- Add a YouTube archive-note renderer modeled after the Instagram archive notes, but adapted for YouTube:
  - YAML frontmatter with source metadata
  - short summary near the top, constrained to fewer than 50 words
  - long summary near the top, written as 3-5 paragraphs when the content justifies it
  - description/body content
  - thumbnail and a YouTube embedded player if Obsidian-compatible HTML/embed markdown works cleanly, without downloading the video itself
  - metadata footer with original URL and watch/archive details
  - `## Transcript` section at the bottom containing downloaded subtitles/transcript inline when available
- Store downloaded assets under `<vault>/Social Archives/attachments/youtube/<year>/<video-id>/...`.
- Video summaries and tags should be generated from all available context: title, description, subtitles/transcript, and top 20 comments.
- Daily notes should contain only a 3-5 paragraph day summary plus tags for everything watched that day; the day summary can use all available info, including the per-video summaries, and daily tags should be the union/aggregation of the video tags.
- Keep resume/cache behavior, but shift the cache source of truth to the per-video archive note and/or a dedicated embedded cache block so interrupted runs can continue without re-fetching everything.

## Files to modify
- `PLAN.md`
- Likely implementation files after approval:
  - `youtube_watch_history_importer/cli.py`
  - `tests/test_cli.py`
  - `README.md`
  - possibly new helpers/modules for archive-note rendering and subtitle handling

## Reuse
- Existing importer logic already present in `youtube_watch_history_importer/cli.py`:
  - Google Takeout archive reading
  - Obsidian vault + daily-notes config resolution
  - note path formatting
  - yt-dlp metadata fetching
  - Ollama summary generation
  - cache/resume behavior
- Existing archive-note pattern found in Instagram sample:
  - `tmp/obsvlt/Social Archives/Instagram/2025/10/2025-10-08 - Laura Anderson - THIS is the cheat code to being your own boss!! Ne- (E-kTrv).md`
  - frontmatter includes platform/author/published/archived/originalUrl/engagement fields
  - body includes original text, media embed, and metadata footer
- Existing media attachment layout found in Instagram sample:
  - `tmp/obsvlt/attachments/social-archives/instagram/<post-id>/...`
- Existing daily note sample for rollup target:
  - `tmp/obsvlt/Daily Notes/2026/03/2026-03-06.md`

## Steps
- [ ] Inspect more Instagram archive samples and infer the common note/attachment conventions to mirror for YouTube.
- [ ] Define YouTube archive note schema: filename pattern `YYYY-MM-DD - Channel - Title - (video-id).md`, frontmatter fields, attachment layout, transcript placement, and summary sections.
- [ ] Define daily-note rollup format for watched videos per day: `## YouTube Watch History` at the bottom, containing a 3-5 paragraph day summary plus aggregated tags only.
- [ ] Extend metadata fetching to include subtitles/transcripts when available and top 20 comments, and feed all of that into summary/tag generation.
- [ ] Implement per-video archive note generation under `Social Archives/YouTube`.
- [ ] Implement media/thumbnail embed rendering, including evaluating a YouTube embedded player option without downloading the video itself.
- [ ] Implement video summary/tag generation with both a short summary (<50 words) and a long summary (3-5 paragraphs when relevant).
- [ ] Implement daily-note day summary generation based on all watched videos for that date, using per-video summaries and aggregated tags.
- [ ] Update resume/cache behavior to work with per-video archive notes and day rollups.
- [ ] Add tests/fixtures for archive note rendering, transcript handling, attachment paths, embed rendering, summary constraints, and daily rollup rendering/replacement.
- [ ] Verify end-to-end in a temp vault that mirrors the Instagram archive structure.

## Verification
- Dry-run using the takeout archive and print planned archive-note + daily-note outputs.
- Generate at least one YouTube archive note in a temp vault and compare its structure to the Instagram archive pattern.
- Verify attachment paths land under `<vault>/Social Archives/attachments/youtube/<year>/<video-id>/...`.
- Verify subtitles, when available, are downloaded, stored inline under a `## Transcript` subheader, and included in summary input/output.
- Verify per-video summaries use title, description, subtitles, and the top 20 comments; verify the short summary stays under 50 words and the long summary expands to 3-5 paragraphs when relevant.
- Verify daily notes receive a bottom `## YouTube Watch History` section containing only the requested 3-5 paragraph day summary plus aggregated tags, and that reruns replace that section content rather than duplicate it.
- Re-run twice to confirm resume behavior does not duplicate archive notes or daily rollups.
