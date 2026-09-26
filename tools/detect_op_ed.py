"""
Blind OP/ED detector — finds opening/ending by audio fingerprint cross-correlation
across episodes (OP/ED audio repeats every episode, so it shows up as the segment
that matches between neighbouring episodes). Works with NO chapters and regardless
of IntroDB coverage.

Usage:  drag an episode folder onto detect_op_ed.bat  (or run: python detect_op_ed.py "folder")
Output: prints OP/ED timestamps per episode and writes op_ed_detected.json in the folder.
"""
import subprocess, sys, os, re, json, shutil
import numpy as np
from collections import defaultdict

def resolve_executable(name, environment_variable):
    """Use an explicit executable path first, then fall back to PATH."""
    configured = os.environ.get(environment_variable)
    if configured:
        if os.path.isfile(configured):
            return configured
        raise FileNotFoundError(f"{environment_variable} does not point to a file: {configured}")
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"Could not find {name}. Add it to PATH or set {environment_variable} to its full path."
    )

FFMPEG = resolve_executable("ffmpeg", "INTRODB_FFMPEG")
MKVPROPEDIT = resolve_executable("mkvpropedit", "INTRODB_MKVPROPEDIT")
MKVEXTRACT = resolve_executable("mkvextract", "INTRODB_MKVEXTRACT")
SR, FRAME, HOP, NBANDS = 11025, 4096, 1024, 32      # ~0.093s/frame, 31-bit fingerprint
HAM_OK = 3          # max bit-differences to call two frames "same"
MIN_SEG = 12.0      # ignore shared segments shorter than this (s)
EDGE_TRIM = 1.0     # pull each OP/ED boundary inward by this many seconds (drops transition/fade edges)

def load_audio(path):
    cmd = [FFMPEG, "-v", "quiet", "-i", path, "-map", "0:a:0", "-ac", "1",
           "-ar", str(SR), "-f", "s16le", "-"]
    out = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32)

_freqs = np.fft.rfftfreq(FRAME, 1 / SR)
_edges = np.logspace(np.log10(150), np.log10(3500), NBANDS + 1)
_bandsel = [np.where((_freqs >= _edges[m]) & (_freqs < _edges[m + 1]))[0] for m in range(NBANDS)]
_win = np.hanning(FRAME).astype(np.float32)

def fingerprint(a):
    if len(a) < FRAME * 2:
        return np.zeros(0, np.uint32)
    n = 1 + (len(a) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(a[idx] * _win, axis=1))
    bands = np.empty((n, NBANDS), np.float32)
    for m, sel in enumerate(_bandsel):
        bands[:, m] = spec[:, sel].mean(axis=1) if len(sel) else 0.0
    bands = np.log1p(bands)
    d = bands[:, :-1] - bands[:, 1:]          # n x (NBANDS-1)
    dd = d[1:] - d[:-1]                        # (n-1) x (NBANDS-1)
    bits = (dd > 0)
    fp = np.zeros(bits.shape[0], np.uint32)
    for k in range(bits.shape[1]):
        fp |= bits[:, k].astype(np.uint32) << k
    feat = bands[1:]                           # align with fp (length n-1)
    feat = feat / (np.linalg.norm(feat, axis=1, keepdims=True) + 1e-9)
    return fp, feat.astype(np.float32)          # hash + unit-norm spectral feature per ~0.093s

def popcount(x):
    x = np.asarray(x, dtype=np.uint64)
    x = x - ((x >> 1) & 0x55555555)
    x = (x & 0x33333333) + ((x >> 2) & 0x33333333)
    x = (x + (x >> 4)) & 0x0F0F0F0F
    return (((x * 0x01010101) & 0xFFFFFFFF) >> 24).astype(np.uint32)   # &mask replicates the 32-bit fold

def frame_to_sec(k):
    return (k * HOP + FRAME / 2) / SR

def refine_bounds(fpA, fpB, off, c0, c1, ham_thr=7, max_gap=15):
    """Grow a matched seed [c0,c1] outward at fixed offset while the fingerprint still
    matches B within ham_thr bits (identical audio ~0-8; unrelated audio ~14-16).
    Bridges brief loud SFX (<=max_gap frames) so quiet intros / fades don't cut it short."""
    N, M = len(fpA), len(fpB)
    def ham(i):
        j = i - off
        if i < 0 or i >= N or j < 0 or j >= M: return 99
        return int(popcount(fpA[i] ^ fpB[j]))
    s = c0; gap = 0
    while s - 1 >= 0:
        if ham(s - 1) <= ham_thr: s -= 1; gap = 0
        else:
            gap += 1; s -= 1
            if gap > max_gap: break
    while s < c0 and ham(s) > ham_thr: s += 1
    e = c1; gap = 0
    while e < N:
        if ham(e) <= ham_thr: e += 1; gap = 0
        else:
            gap += 1; e += 1
            if gap > max_gap: break
    while e > c1 and ham(e - 1) > ham_thr: e -= 1
    # tighten the TRAILING edge only: pull the end back to where the match is STRONG, which
    # drops the shared-preview bleed after the ED. (Leading edge left alone -- refine already
    # stops it cleanly at the cold-open/episode transition; trimming it would push starts late.)
    strict, win = 3, 5
    while e - 1 > s:
        w = [ham(k) for k in range(max(s, e - win), e)]
        if sum(w) / len(w) <= strict: break
        e -= 1
    return s, e

def shared_segments(fpA, fpB, featA, featB):
    """Return shared [startA,endA] segments (seconds), boundaries refined by spectral match."""
    if len(fpA) == 0 or len(fpB) == 0:
        return []
    posB = defaultdict(list)
    for j, h in enumerate(fpB.tolist()):
        posB[h].append(j)
    votes = defaultdict(int)
    for i, h in enumerate(fpA.tolist()):
        for j in posB.get(h, ()):
            votes[i - j] += 1
    min_f = int(MIN_SEG / (HOP / SR))
    segs = []
    for off, v in sorted(votes.items(), key=lambda kv: -kv[1])[:12]:
        i0, i1 = max(0, off), min(len(fpA), len(fpB) + off)
        if i1 - i0 < min_f * 0.4:
            continue
        good = popcount(fpA[i0:i1] ^ fpB[i0 - off:i1 - off]) <= HAM_OK
        runs = []; run_s = None; gap = 0                     # collect ALL matched runs (OP and ED can share an offset)
        for k, g in enumerate(good):
            if g:
                if run_s is None: run_s = k
                gap = 0
            else:
                gap += 1
                if run_s is not None and gap > 8:
                    runs.append((run_s, k - gap)); run_s = None
        if run_s is not None:
            runs.append((run_s, len(good)))
        for a, b in runs:
            if b - a < 3:                                    # tiny seed OK -- refinement grows real ones
                continue
            s, e = refine_bounds(fpA, fpB, off, i0 + a, i0 + b)
            if frame_to_sec(e) - frame_to_sec(s) >= MIN_SEG:  # keep only if it grew to a real segment
                segs.append((frame_to_sec(s), frame_to_sec(e)))
    segs.sort(key=lambda s: -(s[1] - s[0]))
    kept = []
    for s in segs:
        if not any(min(s[1], k[1]) - max(s[0], k[0]) > 5 for k in kept):
            kept.append(s)
    return kept

def sec2ts(s):
    h = int(s // 3600); m = int(s % 3600 // 60)
    return f"{h:02d}:{m:02d}:{s - h*3600 - m*60:06.3f}"

def parse_ts(ts):
    m = re.match(r'(\d+):(\d+):(\d+(?:\.\d+)?)$', ts.strip())
    if not m:
        raise ValueError(f"Bad chapter timestamp: {ts}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

def parse_user_time(value):
    """Accept seconds, MM:SS, or HH:MM:SS style timestamps."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Blank timestamp")
    if re.match(r'^\d+(?:\.\d+)?$', raw):
        return float(raw)
    parts = raw.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Bad timestamp: {value}")

def sec2short(s):
    if s is None:
        return "?"
    s = float(s)
    h = int(s // 3600)
    m = int(s % 3600 // 60)
    sec = s - h * 3600 - m * 60
    if h:
        return f"{h}:{m:02d}:{sec:06.3f}".rstrip("0").rstrip(".")
    return f"{m}:{sec:06.3f}".rstrip("0").rstrip(".")

def chapter_list(op, ed, dur):
    """OP begin/end + ED begin/end (or ED to EOF). Adds Intro/Episode/Preview around them."""
    ch = []
    if op:
        if op[0] > 3: ch.append((0.0, "Intro"))
        ch += [(op[0], "OP"), (op[1], "Episode")]
    else:
        ch.append((0.0, "Episode"))
    if ed:
        ch.append((ed[0], "ED"))
        if ed[1] < dur - 3: ch.append((ed[1], "Preview"))   # else ED runs to EOF -> no marker after
    return [c for i, c in enumerate(ch) if i == 0 or c[0] > ch[i-1][0] + 0.05]

def write_chapters(path, ch, dur):
    if path.lower().endswith(".mkv"):
        x = ['<?xml version="1.0" encoding="UTF-8"?>', '<!DOCTYPE Chapters SYSTEM "matroska.dtd">', "<Chapters>", "<EditionEntry>"]
        for t, l in ch:
            x += ["<ChapterAtom>", f"<ChapterTimeStart>{sec2ts(t)}</ChapterTimeStart>",
                  f"<ChapterDisplay><ChapterString>{l}</ChapterString><ChapterLanguage>eng</ChapterLanguage></ChapterDisplay>", "</ChapterAtom>"]
        x += ["</EditionEntry>", "</Chapters>"]
        tmp = path + ".chapters.xml"; open(tmp, "w", encoding="utf-8").write("\n".join(x))
        rc = subprocess.run([MKVPROPEDIT, path, "--chapters", tmp], capture_output=True).returncode
        os.remove(tmp); return rc == 0
    else:  # MP4/AVI -> lossless MKV remux with chapter metadata. AVI cannot retain chapters.
        times = [t for t, _ in ch] + [dur]
        meta = [";FFMETADATA1"]
        for i, (t, l) in enumerate(ch):
            meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(t*1000)}", f"END={int(times[i+1]*1000)}", f"title={l}"]
        mf = path + ".ffmeta"; open(mf, "w", encoding="utf-8").write("\n".join(meta))
        out = os.path.splitext(path)[0] + ".mkv"
        tmp = out + ".tmp.mkv"
        rc = subprocess.run([FFMPEG, "-v", "quiet", "-y", "-fflags", "+genpts", "-i", path, "-i", mf,
                             "-map", "0", "-map_metadata", "0", "-map_chapters", "1", "-c", "copy", tmp],
                            capture_output=True).returncode
        os.remove(mf)
        if rc == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
            os.replace(tmp, out); return True
        if os.path.exists(tmp): os.remove(tmp)
        return False

def read_existing_chapters(path):
    if not path.lower().endswith(".mkv"):
        return None
    proc = subprocess.run([MKVEXTRACT, path, "chapters", "--simple", "-"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    by_id = {}
    for line in proc.stdout.splitlines():
        m = re.match(r'^CHAPTER(\d+)=(.+)$', line)
        if m:
            by_id.setdefault(m.group(1), {})["time"] = parse_ts(m.group(2))
            continue
        m = re.match(r'^CHAPTER(\d+)NAME=(.*)$', line)
        if m:
            by_id.setdefault(m.group(1), {})["name"] = m.group(2)
    return [(v["time"], v.get("name", "")) for _, v in sorted(by_id.items(), key=lambda kv: int(kv[0])) if "time" in v]

def write_existing_chapters(path, ch):
    if not path.lower().endswith(".mkv"):
        return False
    x = ['<?xml version="1.0" encoding="UTF-8"?>', '<!DOCTYPE Chapters SYSTEM "matroska.dtd">', "<Chapters>", "<EditionEntry>"]
    for t, l in ch:
        x += ["<ChapterAtom>", f"<ChapterTimeStart>{sec2ts(t)}</ChapterTimeStart>",
              f"<ChapterDisplay><ChapterString>{l}</ChapterString><ChapterLanguage>eng</ChapterLanguage></ChapterDisplay>", "</ChapterAtom>"]
    x += ["</EditionEntry>", "</Chapters>"]
    tmp = path + ".chapters.xml"
    open(tmp, "w", encoding="utf-8").write("\n".join(x))
    rc = subprocess.run([MKVPROPEDIT, path, "--chapters", tmp], capture_output=True).returncode
    os.remove(tmp)
    return rc == 0

def epnum(fn):
    m = re.search(r'S\d\dE(\d{1,3})', fn, re.I)
    if m: return int(m.group(1))
    m = re.search(r'(?:^|[^A-Za-z0-9])Ep(?:isode)?\s*_?(\d{1,3})(?:v\d)?(?=[-_ .\(\)\[\]]|$)', fn, re.I)
    if m: return int(m.group(1))
    m = re.search(r'\[(\d{1,3})(?:v\d)?\]', fn, re.I)
    if m: return int(m.group(1))
    # digits delimited by a separator and NOT followed by a letter (avoids 720p, x264, hex ids)
    cands = re.findall(r'[-_ ]\s*_*(\d{1,3})(?:v\d)?(?=[-_ .\(\)\[\]]|$)', fn)   # tolerate a vN version suffix (e.g. 07v2)
    return int(cands[-1]) if cands else None

def parse_removals(spec):
    """Parse removal specs like: 12, 12:both, 3:op, 7:ed, 1-3:op."""
    removals = defaultdict(set)
    if not spec:
        return removals
    for part in re.split(r'[,; ]+', spec.strip()):
        if not part:
            continue
        if ":" in part:
            ep_part, kind_part = part.split(":", 1)
        else:
            ep_part, kind_part = part, "both"
        kind_part = kind_part.lower()
        kinds = set()
        if kind_part in ("both", "all", "op+ed", "oped"):
            kinds = {"intro", "outro"}
        else:
            if "op" in kind_part or "intro" in kind_part:
                kinds.add("intro")
            if "ed" in kind_part or "outro" in kind_part:
                kinds.add("outro")
        if not kinds:
            continue
        m = re.match(r'^(\d{1,3})-(\d{1,3})$', ep_part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            eps = range(a, b + 1)
        elif ep_part.isdigit():
            eps = [int(ep_part)]
        else:
            continue
        for ep in eps:
            removals[ep].update(kinds)
    return removals

def find_episode_files(folder):
    files = {}
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith((".mkv", ".mp4", ".avi")) and not re.search(r'\bNC(?:OP|ED)?\b|Clean|Menu|\bSP\b|Creditless|Extra|Bonus|Special', fn, re.I):
            n = epnum(fn)
            if n is not None:
                files.setdefault(n, os.path.join(folder, fn))
    return files

def normalized_chapter_label(label):
    return re.sub(r'\s+', ' ', (label or "").strip().lower())

def resolve_export_label_choice(present_labels, priority):
    """Pick one release-wide label family for IntroDB export.

    This intentionally mirrors the manual workflow: real OP/ED-style labels win
    across the whole release, and generic Intro/Outro are only fallbacks when no
    real OP/ED label family exists anywhere in the release.
    """
    for display_name, labels, fallback in priority:
        if present_labels.intersection(labels):
            return {"display": display_name, "labels": labels, "fallback": fallback}
    return None

def add_id_fields(row, imdb_id, tvdb_id):
    row["imdb_id"] = imdb_id
    if tvdb_id:
        try:
            row["tvdb_id"] = int(tvdb_id)
        except ValueError:
            row["tvdb_id"] = tvdb_id
    return row

def segment_from_user(value):
    v = normalized_chapter_label(value)
    if v in ("op", "opening", "intro", "i"):
        return "intro"
    if v in ("ed", "ending", "credits", "credits start", "outro", "o"):
        return "outro"
    if v in ("recap", "r"):
        return "recap"
    return None

def episode_from_user(value):
    raw = (value or "").strip()
    if raw.isdigit():
        return int(raw)
    m = re.search(r'S\d+\s*E(\d+)', raw, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'Ep(?:isode)?\s*(\d+)', raw, re.I)
    if m:
        return int(m.group(1))
    return None

def slugify_name(name):
    slug = normalized_chapter_label(name)
    slug = slug.replace("&", " and ")
    slug = re.sub(r"['’`]", "", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "show"

def prompt_with_default(label, default=""):
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value if value else default

def discover_export_targets(folder):
    """Return folders that directly contain episode files.

    If the dragged folder itself has episodes, treat it as one show. Otherwise,
    process child folders that directly contain episodes, which supports dragging
    a parent folder with many show folders inside.
    """
    if find_episode_files(folder):
        return [folder]
    targets = []
    for root, dirs, _files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in ("_introdb_exports", "_tools", "fonts", "subs", "extras", "bonus")]
        if root == folder:
            continue
        if find_episode_files(root):
            targets.append(root)
            dirs[:] = []
    return sorted(targets)

def find_chapter_for_override(chapters, selector):
    raw = (selector or "").strip()
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(chapters):
            return idx
        return None
    want = normalized_chapter_label(raw)
    for idx, (_, label) in enumerate(chapters):
        if normalized_chapter_label(label) == want:
            return idx
    return None

def make_export_row(imdb_id, tvdb_id, season_num, episode, segment_type, start, end):
    row = {
        "segment_type": segment_type,
        "season": season_num,
        "episode": int(episode),
        "start_sec": round(float(start), 3),
        "end_sec": round(float(end), 3),
    }
    return add_id_fields(row, imdb_id, tvdb_id)

def validate_export_row(row):
    length = row["end_sec"] - row["start_sec"]
    if length < 5:
        return f"Duration {length:.3f}s is under IntroDB's 5s minimum."
    if length > 180:
        return f"Duration {length:.3f}s is over IntroDB's 180s maximum."
    return None

def upsert_export_row(rows, row):
    key = (row["season"], row["episode"], row["segment_type"])
    for i, existing in enumerate(rows):
        if (existing["season"], existing["episode"], existing["segment_type"]) == key:
            rows[i] = row
            return "updated"
    rows.append(row)
    rows.sort(key=lambda r: (r["season"], r["episode"], {"recap": 0, "intro": 1, "outro": 2}.get(r["segment_type"], 9), r["start_sec"]))
    return "added"

def print_episode_chapters(chapter_data, episode):
    info = chapter_data.get(episode)
    if not info:
        print(f"No chaptered file found for episode {episode}.")
        return
    chapters = info["chapters"]
    print(f"\nEpisode {episode} chapters:")
    for idx, (start, label) in enumerate(chapters, start=1):
        end = chapters[idx][0] if idx < len(chapters) else None
        end_text = sec2short(end) if end is not None else "EOF"
        print(f"  {idx:02}. {sec2short(start)} -> {end_text}  {label}")

def print_export_preview(rows, skipped, intro_choice, outro_choice):
    print("\nIntroDB export preview")
    print(f"Release-wide intro label family: {intro_choice['display'] if intro_choice else 'none'}")
    print(f"Release-wide outro label family: {outro_choice['display'] if outro_choice else 'none'}")
    print(f"Rows: {len(rows)}")
    print("-" * 72)
    for row in sorted(rows, key=lambda r: (r["season"], r["episode"], {"recap": 0, "intro": 1, "outro": 2}.get(r["segment_type"], 9), r["start_sec"])):
        length = row["end_sec"] - row["start_sec"]
        print(f"S{row['season']}E{row['episode']:02} {row['segment_type'].upper():5} {sec2short(row['start_sec'])} -> {sec2short(row['end_sec'])} ({length:.1f}s)")
    if skipped:
        print(f"\nSkipped/warnings: {len(skipped)}. The text report will include details.")

def review_export_rows(rows, skipped, chapter_data, durations, imdb_id, tvdb_id, season_num, intro_choice, outro_choice):
    while True:
        print_export_preview(rows, skipped, intro_choice, outro_choice)
        answer = input("\nIs this correct? Y/N: ").strip().lower()
        if answer in ("y", "yes"):
            return rows
        if answer not in ("n", "no"):
            print("Please type Y or N.")
            continue

        while True:
            print("\nWhat needs to change?")
            print("  1 = Change an episode to a different chapter label/number")
            print("  2 = Edit/add exact timestamps")
            print("  3 = Remove a row")
            print("  4 = Review again")
            choice = input("Choice: ").strip().lower()
            if choice in ("4", "done", "review", ""):
                break

            episode = episode_from_user(input("Episode number (or S1E06): "))
            if episode is None:
                print("Episode must be a number, like 6 or S1E06.")
                continue

            segment_type = segment_from_user(input("Segment (OP/intro, ED/outro, or recap): "))
            if not segment_type:
                print("Segment must be intro/op, outro/ed, or recap.")
                continue

            if choice == "1":
                print_episode_chapters(chapter_data, episode)
                info = chapter_data.get(episode)
                if not info:
                    continue
                selector = input("Correct chapter label or chapter number: ").strip()
                idx = find_chapter_for_override(info["chapters"], selector)
                if idx is None:
                    print("Could not find that chapter label/number.")
                    continue
                start = info["chapters"][idx][0]
                end = info["chapters"][idx + 1][0] if idx + 1 < len(info["chapters"]) else durations.get(episode)
                if end is None:
                    print("Could not determine the end time for that chapter.")
                    continue
                row = make_export_row(imdb_id, tvdb_id, season_num, episode, segment_type, start, end)
                problem = validate_export_row(row)
                if problem:
                    print(f"Not added: {problem}")
                    continue
                status = upsert_export_row(rows, row)
                print(f"{status.capitalize()} S{season_num}E{episode} {segment_type}: {sec2short(start)} -> {sec2short(end)}")
            elif choice == "2":
                try:
                    start = parse_user_time(input("Start time (ex 00:53): "))
                    end = parse_user_time(input("End time (ex 01:53, or exact EOF time): "))
                except ValueError as exc:
                    print(exc)
                    continue
                row = make_export_row(imdb_id, tvdb_id, season_num, episode, segment_type, start, end)
                problem = validate_export_row(row)
                if problem:
                    print(f"Not added: {problem}")
                    continue
                status = upsert_export_row(rows, row)
                print(f"{status.capitalize()} S{season_num}E{episode} {segment_type}: {sec2short(start)} -> {sec2short(end)}")
            elif choice == "3":
                before = len(rows)
                rows[:] = [r for r in rows if not (r["season"] == season_num and r["episode"] == episode and r["segment_type"] == segment_type)]
                print(f"Removed {before - len(rows)} row(s).")
            else:
                print("Unknown choice.")

def write_introdb_export_files(output_dir, show_slug, rows, skipped, imdb_id, tvdb_id, season_num, intro_choice, outro_choice):
    os.makedirs(output_dir, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["season"], r["episode"], {"recap": 0, "intro": 1, "outro": 2}.get(r["segment_type"], 9), r["start_sec"]))
    full_json = os.path.join(output_dir, f"submitted-{show_slug}-introdb.json")
    json.dump({"items": rows}, open(full_json, "w", encoding="utf-8"), indent=2)

    pack_paths = []
    for idx in range(0, len(rows), 100):
        pack_num = idx // 100 + 1
        suffix = "" if pack_num == 1 else f"-{pack_num}"
        pack_path = os.path.join(output_dir, f"unsubmitted-{show_slug}{suffix}-introdb.json")
        json.dump({"items": rows[idx:idx + 100]}, open(pack_path, "w", encoding="utf-8"), indent=2)
        pack_paths.append(pack_path)

    report = os.path.join(output_dir, f"unsubmitted-{show_slug}-introdb-report.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"IntroDB export for {show_slug}\n")
        fh.write(f"IMDb: {imdb_id}\n")
        fh.write(f"TVDB: {tvdb_id or 'not provided'}\n")
        fh.write(f"Season: {season_num}\n")
        fh.write(f"Release-wide intro label family: {intro_choice['display'] if intro_choice else 'none'}\n")
        fh.write(f"Release-wide outro label family: {outro_choice['display'] if outro_choice else 'none'}\n")
        fh.write(f"Rows: {len(rows)}\n")
        fh.write(f"Upload packs: {len(pack_paths)}\n\n")
        for row in rows:
            fh.write(f"S{row['season']}E{row['episode']} {row['segment_type'].upper()} {row['start_sec']} -> {row['end_sec']}\n")
        if skipped:
            fh.write("\nSkipped / warnings:\n")
            for s in skipped:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    return full_json, pack_paths, report

def edit_existing(folder, remove_spec):
    files = find_episode_files(folder)
    if not files:
        print("No episode files found.")
        return
    removals = parse_removals(remove_spec)
    if not removals:
        print("No removals entered. Nothing changed.")
        return
    print(f"Editing existing chapters for {len(files)} episodes...")
    for n in sorted(removals):
        f = files.get(n)
        if not f:
            print(f"  E{n:02}: file not found, skipped")
            continue
        if not f.lower().endswith(".mkv"):
            print(f"  E{n:02}: edit-only mode only supports MKV files, skipped")
            continue
        ch = read_existing_chapters(f)
        if not ch:
            print(f"  E{n:02}: no existing chapters found, skipped")
            continue
        remove_names = set()
        if "intro" in removals[n]:
            remove_names.update(("op", "opening", "opening credits"))
        if "outro" in removals[n]:
            remove_names.update(("ed", "ending", "ending credits", "credits", "credits start"))
        new_ch = [(t, label) for t, label in ch if normalized_chapter_label(label) not in remove_names]
        removed_count = len(ch) - len(new_ch)
        if removed_count == 0:
            print(f"  E{n:02}: no matching chapter labels found")
            continue
        ok = write_existing_chapters(f, new_ch)
        labels = " + ".join(sorted(remove_names))
        print(f"  E{n:02}: {'OK  ' if ok else 'FAIL'} removed {removed_count} chapter(s): {labels}")
    print("\nDone. Existing chapters edited only; no detection was run.")

def export_introdb_json(folder, imdb_id="", tvdb_id="", season="1", show_name="", output_dir=None, interactive=True):
    files = find_episode_files(folder)
    if not files:
        print("No episode files found.")
        return None
    print(f"\n=== Exporting {folder} ===")
    show_name = (show_name or "").strip()
    if interactive and not show_name:
        show_name = prompt_with_default("Show name for filenames", os.path.basename(os.path.normpath(folder)))
    show_name = show_name or os.path.basename(os.path.normpath(folder))
    show_slug = slugify_name(show_name)
    imdb_id = (imdb_id or "").strip()
    if interactive and not imdb_id:
        imdb_id = prompt_with_default("IMDb ID, like tt1234567")
    tvdb_id = (tvdb_id or "").strip()
    if interactive and not tvdb_id:
        tvdb_id = prompt_with_default("TVDB ID (optional)")
    if interactive:
        season = prompt_with_default("Season number", str(season).strip() or "1")
    try:
        season_num = int(str(season).strip() or "1")
    except ValueError:
        print(f"Bad season value: {season}")
        return None
    if not imdb_id:
        print("IMDb ID is required.")
        return None

    items = []
    skipped = []
    chapter_data = {}
    durations = {}
    present_labels = set()

    for n in sorted(files):
        f = files[n]
        if not f.lower().endswith(".mkv"):
            skipped.append({"episode": n, "file": os.path.basename(f), "reason": "Only MKV existing chapters are supported for export."})
            continue
        ch = read_existing_chapters(f)
        if not ch:
            skipped.append({"episode": n, "file": os.path.basename(f), "reason": "No existing chapters found."})
            continue
        chapter_data[n] = {"file": f, "chapters": ch}
        present_labels.update(normalized_chapter_label(label) for _, label in ch)

    intro_choice = resolve_export_label_choice(present_labels, [
        ("OP", {"op"}, False),
        ("Opening", {"opening", "opening credits"}, False),
        ("Intro", {"intro"}, True),
    ])
    outro_choice = resolve_export_label_choice(present_labels, [
        ("ED", {"ed"}, False),
        ("Ending", {"ending", "ending credits"}, False),
        ("Credits Start", {"credits start"}, False),
        ("Credits", {"credits"}, False),
        ("Outro", {"outro"}, True),
    ])

    if not intro_choice:
        skipped.append({"segment_type": "intro", "reason": "No OP/Opening/Intro chapter family found anywhere in the release."})
    if not outro_choice:
        skipped.append({"segment_type": "outro", "reason": "No ED/Ending/Credits/Outro chapter family found anywhere in the release."})

    ignored_intro_fallbacks = {"intro"}
    ignored_outro_fallbacks = {"outro"}

    for n in sorted(chapter_data):
        f = chapter_data[n]["file"]
        ch = chapter_data[n]["chapters"]
        dur = None
        try:
            a = load_audio(f)
            dur = len(a) / SR
        except Exception:
            dur = None
        durations[n] = dur
        for idx, (start, label) in enumerate(ch):
            lower = normalized_chapter_label(label)
            segment_type = None
            if intro_choice and lower in intro_choice["labels"]:
                segment_type = "intro"
            elif outro_choice and lower in outro_choice["labels"]:
                segment_type = "outro"
            if not segment_type:
                if intro_choice and not intro_choice["fallback"] and lower in ignored_intro_fallbacks:
                    skipped.append({
                        "episode": n,
                        "file": os.path.basename(f),
                        "label": label,
                        "reason": f"Ignored generic Intro because release-wide intro label is {intro_choice['display']}."
                    })
                elif outro_choice and not outro_choice["fallback"] and lower in ignored_outro_fallbacks:
                    skipped.append({
                        "episode": n,
                        "file": os.path.basename(f),
                        "label": label,
                        "reason": f"Ignored generic Outro because release-wide outro label is {outro_choice['display']}."
                    })
                continue
            end = ch[idx + 1][0] if idx + 1 < len(ch) else dur
            if end is None:
                skipped.append({"episode": n, "file": os.path.basename(f), "label": label, "reason": "Could not determine chapter end."})
                continue
            row = {
                "segment_type": segment_type,
                "season": season_num,
                "episode": n,
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
            }
            add_id_fields(row, imdb_id, tvdb_id)
            length = row["end_sec"] - row["start_sec"]
            if length < 5 or length > 180:
                skipped.append({"episode": n, "file": os.path.basename(f), "label": label, "start_sec": row["start_sec"], "end_sec": row["end_sec"], "duration": round(length, 3), "reason": "Duration outside IntroDB 5-180s limit."})
                continue
            items.append(row)

    # One intro and one outro max per episode. If a file somehow has duplicate OP/ED labels,
    # keep the first one in chapter order and report the duplicate.
    deduped = []
    seen = set()
    for row in items:
        key = (row["season"], row["episode"], row["segment_type"])
        if key in seen:
            skipped.append({"episode": row["episode"], "segment_type": row["segment_type"], "reason": "Duplicate segment label ignored."})
            continue
        seen.add(key)
        deduped.append(row)

    if interactive:
        deduped = review_export_rows(deduped, skipped, chapter_data, durations, imdb_id, tvdb_id, season_num, intro_choice, outro_choice)

    output_dir = output_dir or folder
    full_json, pack_paths, out_report = write_introdb_export_files(output_dir, show_slug, deduped, skipped, imdb_id, tvdb_id, season_num, intro_choice, outro_choice)
    print(f"Wrote full per-show JSON: {full_json}")
    for pack_path in pack_paths:
        print(f"Wrote upload pack: {pack_path}")
    print(f"Wrote report: {out_report}")
    print(f"Release-wide intro label family: {intro_choice['display'] if intro_choice else 'none'}")
    print(f"Release-wide outro label family: {outro_choice['display'] if outro_choice else 'none'}")
    print(f"Rows: {len(deduped)}")
    if skipped:
        print(f"Skipped/warnings: {len(skipped)} (see report)")
    return {
        "show_name": show_name,
        "show_slug": show_slug,
        "rows": deduped,
        "full_json": full_json,
        "pack_paths": pack_paths,
        "report": out_report,
        "skipped": skipped,
    }

def export_introdb_batch(folder, imdb_id="", tvdb_id="", season="1", show_name="", interactive=True):
    targets = discover_export_targets(folder)
    if not targets:
        print("No episode folders found for export.")
        return
    multi = len(targets) > 1
    output_dir = os.path.join(folder, "_introdb_exports") if multi else None
    print(f"Found {len(targets)} show folder(s) for export.")
    if multi:
        print(f"Combined output folder: {output_dir}")

    results = []
    for idx, target in enumerate(targets, start=1):
        print(f"\nShow folder {idx}/{len(targets)}: {target}")
        result = export_introdb_json(
            target,
            imdb_id=imdb_id if not multi else "",
            tvdb_id=tvdb_id if not multi else "",
            season=season,
            show_name=show_name if not multi else "",
            output_dir=output_dir,
            interactive=interactive,
        )
        if result:
            results.append(result)

    if multi:
        manifest = os.path.join(output_dir, "introdb-export-manifest.json")
        json.dump({
            "shows": [
                {
                    "show_name": r["show_name"],
                    "show_slug": r["show_slug"],
                    "rows": len(r["rows"]),
                    "full_json": os.path.basename(r["full_json"]),
                    "packs": [os.path.basename(p) for p in r["pack_paths"]],
                    "report": os.path.basename(r["report"]),
                }
                for r in results
            ]
        }, open(manifest, "w", encoding="utf-8"), indent=2)
        print(f"\nWrote manifest: {manifest}")
    total_rows = sum(len(r["rows"]) for r in results)
    total_packs = sum(len(r["pack_paths"]) for r in results)
    print(f"\nExport complete: {len(results)} show(s), {total_rows} row(s), {total_packs} upload pack file(s).")

def main(folder, remove_spec=""):
    files = find_episode_files(folder)
    if not files:
        print("No episode files found."); return
    print(f"Fingerprinting {len(files)} episodes...")
    fp = {}; feat = {}; durs = {}
    for n, f in files.items():
        a = load_audio(f); fp[n], feat[n] = fingerprint(a); durs[n] = len(a) / SR
    nums = sorted(files)

    def merge(segs):
        segs = sorted(segs)
        out = []
        for s in segs:
            # An OP can contain a brief episode-specific transition or title-card
            # overlay. Keep its matched islands together rather than treating the
            # stable later music as the start of the OP.
            if out and s[0] <= out[-1][1] + 12:
                out[-1] = (out[-1][0], max(out[-1][1], s[1]))
            else:
                out.append(list(s))
        return out

    results = {}
    for n in nums:
        # Inspect the full nearby window. A transition can hide part of an OP
        # against one neighbour while it remains clear two or three episodes away.
        refs = [m for m in (n - 3, n - 2, n - 1, n + 1, n + 2, n + 3) if m in fp]
        allsegs = []
        for m in refs:
            allsegs += shared_segments(fp[n], fp[m], feat[n], feat[m])
        allsegs = merge(allsegs)
        dur = frame_to_sec(len(fp[n]))
        op = min((s for s in allsegs if s[0] < dur * 0.45), key=lambda s: -(s[1] - s[0]), default=None)
        ed = max((s for s in allsegs if s[1] > dur * 0.55), key=lambda s: (s[1] - s[0]), default=None)
        results[n] = {"intro": [round(op[0], 1), round(op[1], 1)] if op else None,
                      "outro": [round(ed[0], 1), round(ed[1], 1)] if ed else None}
        def fmt(x): return f"{x[0]:.0f}-{x[1]:.0f}s ({x[1]-x[0]:.0f}s)" if x else "-"
        print(f"  E{n:02}: OP {fmt(op):18} ED {fmt(ed)}")

    # Pull only the trailing OP edge inward. The leading OP edge is the first
    # matched music frame and must remain intact; trimming it makes starts late.
    for n in nums:
        r = results[n]
        for seg in ("intro", "outro"):
            if r[seg] and (r[seg][1] - r[seg][0]) > 2 * EDGE_TRIM + 2:
                if seg == "intro":
                    r[seg] = [round(r[seg][0], 1), round(r[seg][1] - EDGE_TRIM, 1)]
                else:
                    r[seg] = [round(r[seg][0] + EDGE_TRIM, 1), round(r[seg][1] - EDGE_TRIM, 1)]

    # consensus post-processing: flag length outliers. Do NOT invent markers -- if OP/ED wasn't
    # actually detected, leave it empty (better no marker than a guessed one).
    def med(xs): return sorted(xs)[len(xs)//2] if xs else None
    opl = med([r["intro"][1]-r["intro"][0] for r in results.values() if r["intro"]])
    edl = med([r["outro"][1]-r["outro"][0] for r in results.values() if r["outro"]])
    ed_start = med([r["outro"][0] for r in results.values() if r["outro"]])
    for n in nums:
        r = results[n]
        for seg, m in (("intro", opl), ("outro", edl)):
            if r[seg] and m and abs((r[seg][1]-r[seg][0]) - m) > 0.3*m:
                r[seg+"_flag"] = "verify (length off vs median)"
    print()
    for n in nums:
        r = results[n]
        def fmt(seg):
            x=r[seg]; f=r.get(seg+"_flag")
            return (f"{x[0]:.0f}-{x[1]:.0f}s ({x[1]-x[0]:.0f}s)" if x else "-") + (f"  <-- {f}" if f else "")
        print(f"  E{n:02}: OP {fmt('intro'):22} | ED {fmt('outro')}")
    op_text = f"{opl:.0f}s" if opl is not None else "none"
    ed_text = f"{edl:.0f}s" if edl is not None else "none"
    print(f"\nOP ~{op_text}, ED ~{ed_text} (medians). Lines flagged 'verify' are outliers worth an eyeball.")
    removals = parse_removals(remove_spec)
    if removals:
        print("\nApplying manual removals before writing chapters:")
        for n in sorted(removals):
            if n not in results:
                print(f"  E{n:02}: not found, skipped")
                continue
            labels = []
            if "intro" in removals[n]:
                results[n]["intro"] = None
                labels.append("OP")
            if "outro" in removals[n]:
                results[n]["outro"] = None
                labels.append("ED")
            print(f"  E{n:02}: removed {' + '.join(labels)}")
    json.dump(results, open(os.path.join(folder, "op_ed_detected.json"), "w"), indent=1)
    print("Wrote op_ed_detected.json")

    if "--detect-only" in sys.argv:
        return
    print("\nWriting OP/ED chapter markers into the files...")
    for n in nums:
        r = results[n]
        op, ed = r.get("intro"), r.get("outro")
        ch = chapter_list(op, ed, durs[n])
        ok = write_chapters(files[n], ch, durs[n])
        eof = " (ED->EOF)" if ed and ed[1] >= durs[n]-3 else ""
        print(f"  E{n:02}: {'OK  ' if ok else 'FAIL'} {' | '.join(f'{sec2ts(t)[3:]} {l}' for t,l in ch)}{eof}")
    print("\nDone. Chapters written (MKV in-place; MP4/AVI losslessly remuxed to chaptered MKV copies). op_ed_detected.json also saved.")

if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    remove_spec = ""
    if "--remove" in sys.argv:
        i = sys.argv.index("--remove")
        if i + 1 < len(sys.argv):
            remove_spec = sys.argv[i + 1]
    if "--export-introdb" in sys.argv:
        def arg_value(name, default=""):
            if name in sys.argv:
                i = sys.argv.index(name)
                if i + 1 < len(sys.argv):
                    return sys.argv[i + 1]
            return default
        export_introdb_batch(
            folder,
            imdb_id=arg_value("--imdb"),
            tvdb_id=arg_value("--tvdb"),
            season=arg_value("--season", "1"),
            show_name=arg_value("--show-name"),
            interactive="--no-review" not in sys.argv,
        )
    elif "--edit-existing" in sys.argv:
        edit_existing(folder, remove_spec)
    else:
        main(folder, remove_spec)
