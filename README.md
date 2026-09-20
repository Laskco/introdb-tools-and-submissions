# IntroDB Tools and Submissions

> Personal archival repository. It is published for reference only; no support, issue triage, pull-request review, or feature work is provided.

Windows utilities for finding or relabeling opening and ending chapter markers, plus finalized IntroDB submission JSON archives.

## Included tools

- `tools/detect_op_ed.bat`: detects repeated OP/ED audio in an episode folder, writes chapter markers, and can export reviewed IntroDB JSON.
- `tools/relabel_chapters.bat`: keeps existing chapter timestamps and relabels detected opening and ending intervals as `OP` and `ED`.

Both launchers create a local `.venv` and install NumPy on first run. Drag an episode folder onto the appropriate `.bat` file.

## Scope and Reliability

These tools were built and tested primarily against anime releases. They have not been tested on non-anime television, movies, live action, or other media, so do not assume the same results outside anime.

There is no measured universal success rate. Results depend on the release, chapter quality, repeated audio, alternate openings/endings, recaps, cold opens, and episode-specific edits. Always review the generated markers before using them for a submission.

- **OP/ED detector:** compares episode audio to locate recurring openings and endings, then writes `OP` and `ED` chapter markers. It is most useful for standard anime seasons whose opening and ending music recurs across episodes. It can miss or shorten segments with unique audio, abrupt edits, or unusual episode structure.
- **Chapter relabeler:** preserves existing chapter timestamps and relabels the intervals the detector identifies as `OP` and `ED`. It is most reliable when the release already has accurate chapter boundaries. It does not create trustworthy timing where the source chapters are wrong or absent.

## Requirements

- Python 3
- `ffmpeg`, `ffprobe`, `mkvpropedit`, and `mkvextract` available on `PATH`
- Or set these environment variables to full executable paths: `INTRODB_FFMPEG`, `INTRODB_FFPROBE`, `INTRODB_MKVPROPEDIT`, and `INTRODB_MKVEXTRACT`

The detector modifies chapter metadata in place. Review generated markers before submitting anything.

## Submission archive

`introdb-submissions/` holds one finalized JSON archive per submitted show. These files contain IMDb/TVDB identifiers, season and episode numbers, segment types, and timestamps only. No API keys, browser sessions, accounts, media files, or local machine paths are included.
