# IntroDB Tools and Submissions

> Personal archival repository. It is published for reference only; no support, issue triage, pull-request review, or feature work is provided.

Windows utilities for preparing anime opening and ending timestamps for IntroDB, plus finalized IntroDB submission JSON archives.

## Included tools

- `tools/detect_op_ed.bat`: detects repeated OP/ED audio in an episode folder, writes chapter markers, and can export reviewed IntroDB JSON.
- `tools/relabel_chapters.bat`: keeps existing chapter timestamps and relabels detected opening and ending intervals as `OP` and `ED`.

Both launchers create a local `.venv` and install NumPy on first run. Drag an episode folder onto the appropriate `.bat` file.

## What the Tools Are For

- **OP/ED detector:** primarily for anime episode folders that have no usable chapters. It compares audio across episodes to find repeated opening and ending sequences, writes `OP`, `Episode`, `ED`, and `Preview` chapter markers, and can export the reviewed timestamps as IntroDB JSON.
- **Chapter relabeler:** primarily for anime releases that already have correctly timed but generic or numbered chapter markers. It identifies which existing intervals correspond to the OP and ED, then changes only those labels to `OP` and `ED` while retaining the release's original chapter positions.

## Scope and Reliability

These tools were built and tested primarily against anime releases. They have not been tested on non-anime television, movies, live action, or other media, so do not assume the same results outside anime.

The detector has no single published accuracy percentage because it has not been benchmarked against a broad, independent set of manually verified releases. Its practical success depends on the release, repeated audio, alternate openings/endings, recaps, cold opens, and episode-specific edits. Review generated markers before using them for a submission.

The relabeler's timestamp-preservation rate is **100% by design**: it keeps the existing chapter timecodes and changes only their labels. Its OP/ED classification still depends on the detector and on the source release having accurate chapter boundaries.

## Requirements

- Python 3
- `ffmpeg`, `ffprobe`, `mkvpropedit`, and `mkvextract` available on `PATH`
- Or set these environment variables to full executable paths: `INTRODB_FFMPEG`, `INTRODB_FFPROBE`, `INTRODB_MKVPROPEDIT`, and `INTRODB_MKVEXTRACT`

The detector modifies chapter metadata in place. Review generated markers before submitting anything.

## Submission archive

`introdb-submissions/` holds one finalized JSON archive per submitted show. These files contain IMDb/TVDB identifiers, season and episode numbers, segment types, and timestamps only. No API keys, browser sessions, accounts, media files, or local machine paths are included.
