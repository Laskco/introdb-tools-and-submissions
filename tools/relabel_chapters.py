"""
Relabel existing chapters as OP/ED (keeps the file's exact timestamps).

For releases that ALREADY have chapter marks in the right spots but with generic labels
(Chapter 1, 2, 3...), this does NOT move anything -- it uses the audio detector only to
figure out WHICH existing marks are the OP and ED, then snaps to the nearest existing mark
and relabels it. So you keep the release author's frame-exact timestamps and just get
OP / Episode / ED / Preview labels.

If a show has NO chapters, use detect_op_ed.bat instead.

Usage: drag a folder onto relabel_chapters.bat  (or: python relabel_chapters.py "folder")
"""
import subprocess, sys, os, re, json, statistics
import detect_op_ed as D   # reuse the audio-detection primitives

FFPROBE = D.resolve_executable("ffprobe", "INTRODB_FFPROBE")
SNAP = 6.0   # max seconds to snap a detected OP/ED boundary onto an existing chapter mark

def read_chapters(f):
    o = subprocess.run([FFPROBE, "-v", "quiet", "-show_chapters", "-of", "json", f],
                       capture_output=True).stdout
    try:
        d = json.loads(o)
    except Exception:
        return []
    return [(round(float(c["start_time"]), 3), (c.get("tags") or {}).get("title", ""))
            for c in d.get("chapters", []) if "start_time" in c]

def detect_all(folder):
    files = {}
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith((".mkv", ".mp4", ".avi")) and not re.search(
                r'\bNC(?:OP|ED)?\b|Clean|Menu|\bSP\b|Creditless|Extra|Bonus|Special', fn, re.I):
            n = D.epnum(fn)
            if n is not None:
                files.setdefault(n, os.path.join(folder, fn))
    fp = {}; feat = {}; durs = {}
    for n, f in files.items():
        a = D.load_audio(f); fp[n], feat[n] = D.fingerprint(a); durs[n] = len(a) / D.SR
    def merge(segs):
        segs = sorted(segs); out = []
        for s in segs:
            if out and s[0] <= out[-1][1] + 3:
                out[-1] = (out[-1][0], max(out[-1][1], s[1]))
            else:
                out.append(list(s))
        return out
    results = {}
    for n in sorted(files):
        refs = [m for m in (n-3, n-2, n-1, n+1, n+2, n+3) if m in fp][:4]
        allsegs = []
        for m in refs:
            allsegs += D.shared_segments(fp[n], fp[m], feat[n], feat[m])
        allsegs = merge(allsegs)
        d = durs[n]
        op = min((s for s in allsegs if s[0] < d * 0.45), key=lambda s: -(s[1]-s[0]), default=None)
        ed = max((s for s in allsegs if s[1] > d * 0.55), key=lambda s: (s[1]-s[0]), default=None)
        results[n] = {"intro": op, "outro": ed}

    # Global fallback pass: some shows rotate multiple OP/ED songs across
    # NON-adjacent episodes (e.g. PaniPoni Dash), so an episode's OP or ED may
    # not match any immediate neighbor even though the same song appears
    # elsewhere in the season. For episodes still missing an OP or ED, match
    # against ALL episodes. Kept conservative so we recover real rotated
    # OP/EDs without ever inventing a mark: the recovered segment must sit in a
    # tight positional zone (OP in first 20%, ED start in last 20%) and be at
    # least 60% as long as this show's typical OP/ED -- that length + position
    # bar excludes mid-episode insert songs and stray coincidental matches.
    oplens = [r["intro"][1]-r["intro"][0] for r in results.values() if r["intro"]]
    edlens = [r["outro"][1]-r["outro"][0] for r in results.values() if r["outro"]]
    op_floor = 0.6 * statistics.median(oplens) if oplens else 30.0
    ed_floor = 0.6 * statistics.median(edlens) if edlens else 30.0
    for n in sorted(files):
        d = durs[n]
        need_op = results[n]["intro"] is None
        need_ed = results[n]["outro"] is None
        if not (need_op or need_ed):
            continue
        wide = []
        for m in files:
            if m == n:
                continue
            wide += D.shared_segments(fp[n], fp[m], feat[n], feat[m])
        wide = merge(wide)
        if need_op:
            c = [s for s in wide if s[0] < d * 0.20 and (s[1]-s[0]) >= op_floor]
            if c:
                results[n]["intro"] = max(c, key=lambda s: s[1]-s[0])
        if need_ed:
            c = [s for s in wide if s[0] > d * 0.80 and (s[1]-s[0]) >= ed_floor]
            if c:
                results[n]["outro"] = max(c, key=lambda s: s[1]-s[0])
    return files, durs, results

def best_interval(intervals, seg):
    """index of the chapter interval that the detected OP/ED overlaps most."""
    if not seg:
        return None
    bi, bo = None, 0.0
    for i, (a, b) in enumerate(intervals):
        ov = max(0.0, min(b, seg[1]) - max(a, seg[0]))
        if ov > bo:
            bo, bi = ov, i
    return bi if bo >= 10 else None      # need real overlap, not a stray touch

def main(folder):
    files, durs, results = detect_all(folder)
    if not files:
        print("No episode files found."); return
    print(f"Detecting + relabeling {len(files)} episodes...\n")

    # ---- phase A: read chapters, snap the audio-detected OP/ED onto marks ----
    ep = {}                       # n -> episode state, or None if no chapters
    op_lens, ed_lens = [], []     # lengths of the marks audio was confident about
    for n in sorted(files):
        f = files[n]
        existing = read_chapters(f)
        if not existing:
            ep[n] = None
            continue
        times = [t for t, _ in existing]
        bounds = times + [durs[n]]
        intervals = [(bounds[i], bounds[i+1]) for i in range(len(times))]
        opi = best_interval(intervals, results[n]["intro"])
        edi = best_interval(intervals, results[n]["outro"])
        if opi is not None: op_lens.append(intervals[opi][1] - intervals[opi][0])
        if edi is not None: ed_lens.append(intervals[edi][1] - intervals[edi][0])
        ep[n] = dict(f=f, existing=existing, times=times, intervals=intervals,
                     opi=opi, edi=edi)

    # ---- structural fallback for episodes audio couldn't cross-match ----
    # These releases cut chapter marks EXACTLY at the OP/ED song boundaries, so
    # every OP/ED chapter is the same length: the song length. Some episodes use
    # a one-off OP/ED (a gag song used nowhere else), so audio has nothing to
    # match -- but the correctly-placed mark is still sitting in the chapter
    # list. Learn the show's OP/ED length from the episodes audio DID nail, then
    # fill a miss with the existing chapter whose length matches. This only ever
    # selects a real releaser-placed mark; it never invents a timestamp.
    op_len = statistics.median(op_lens) if op_lens else None
    ed_len = statistics.median(ed_lens) if ed_lens else None
    TOL = 4.0                     # marks are frame-exact, so the length match is tight
    for n in sorted(files):
        e = ep[n]
        if not e:
            continue
        d = durs[n]; iv = e["intervals"]
        def length_match(target, lo, hi, exclude):
            c = [i for i, (a, b) in enumerate(iv)
                 if lo < a < hi and i != exclude and abs((b - a) - target) <= TOL]
            # closest to the song length wins; ties break toward the later mark
            # (skips a cold-open that happens to be ~OP length, favors a real ED)
            return min(c, key=lambda i: (abs((iv[i][1]-iv[i][0]) - target), -i)) if c else None
        if e["opi"] is None and op_len:
            e["opi"] = length_match(op_len, 0.0, d * 0.45, e["edi"])
        if e["edi"] is None and ed_len:
            e["edi"] = length_match(ed_len, d * 0.45, d, e["opi"])

    # ---- phase B: apply labels and write ----
    for n in sorted(files):
        e = ep[n]
        if e is None:
            print(f"  E{n:02}: NO existing chapters -- use detect_op_ed.bat for this show")
            continue
        existing, times, intervals = e["existing"], e["times"], e["intervals"]
        opi, edi = e["opi"], e["edi"]
        new = [l for _, l in existing]
        d = durs[n]
        if opi is not None: new[opi] = "OP"
        if edi is not None: new[edi] = "ED"
        if opi is not None and opi + 1 < len(new) and opi + 1 != edi: new[opi+1] = "Episode"
        # only call the mark after the ED a "Preview" when the ED is a real
        # end-credits sequence (last ~20%); an early ED is followed by more show
        if (edi is not None and edi + 1 < len(new) and edi + 1 != opi
                and intervals[edi+1][0] > d * 0.80):
            new[edi+1] = "Preview"
        # a leading mark before the OP is the cold-open -> label it Intro
        if opi and times[0] < 5 and new[0] not in ("OP", "Episode", "ED", "Preview"):
            new[0] = "Intro"
        ch = list(zip(times, new))
        ok = D.write_chapters(e["f"], ch, durs[n])
        def mm(s): return f"{int(s//60)}:{s%60:04.1f}"
        opm = next((mm(t) for t, l in ch if l == "OP"), "-")
        edm = next((mm(t) for t, l in ch if l == "ED"), "-")
        matched = (opm != "-") or (edm != "-")
        print(f"  E{n:02}: {'OK  ' if ok else 'FAIL'} OP@{opm}  ED@{edm}  ({len(existing)} marks)"
              + ("" if matched else "  <-- no OP/ED mark found (show may not fit this tool)"))
    print("\nDone. Existing chapter timestamps kept; OP/ED marks relabeled in place.")

if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    main(folder)
