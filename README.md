# IntroDB Tools and Submissions

> Personal archival repository. It is published for reference only; no support, issue triage, pull-request review, or feature work is provided.

Windows utilities for finding or relabeling opening and ending chapter markers, plus finalized IntroDB submission JSON archives.

## Included tools

- `tools/detect_op_ed.bat`: detects repeated OP/ED audio in an episode folder, writes chapter markers, and can export reviewed IntroDB JSON.
- `tools/relabel_chapters.bat`: keeps existing chapter timestamps and relabels detected opening and ending intervals as `OP` and `ED`.

Both launchers create a local `.venv` and install NumPy on first run. Drag an episode folder onto the appropriate `.bat` file.

## Requirements

- Python 3
- `ffmpeg`, `ffprobe`, `mkvpropedit`, and `mkvextract` available on `PATH`
- Or set these environment variables to full executable paths: `INTRODB_FFMPEG`, `INTRODB_FFPROBE`, `INTRODB_MKVPROPEDIT`, and `INTRODB_MKVEXTRACT`

The detector modifies chapter metadata in place. Review generated markers before submitting anything.

## Submission archive

`introdb-submissions/` holds one finalized JSON archive per submitted show. These files contain IMDb/TVDB identifiers, season and episode numbers, segment types, and timestamps only. No API keys, browser sessions, accounts, media files, or local machine paths are included.
