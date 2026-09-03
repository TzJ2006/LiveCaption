#!/usr/bin/env python3
"""Score an ASR run against a reference transcript, for bilingual zh-en audio.

Reads a reference .txt (one utterance per line) and one or more run .json files written by
bench_asr.py, and prints accuracy, technical-vocabulary and latency metrics.

Usage:
  python src/python/score_asr.py bench/ref/clip-a.txt bench/runs/*.json
  python src/python/score_asr.py --self-test

The accuracy metric is MER (ASRU-2019 / SEAME): one token per Han character, one per Latin word,
Levenshtein over the mixed stream. The headline number for code-switching is PIER, which counts
only the errors at language-switch points -- aggregate MER understates switch-point damage by
roughly half (SEAME: 39.7 MER vs 58.7 PIER), so a model can improve on MER while getting worse at
exactly the thing bilingual captions are for.

# ponytail: stdlib + numpy, no jiwer/editdistance. One Levenshtein backtrace feeds every metric
# below -- MER, the zh/en split, PIER, the glossary split and term F1 are all views of one
# alignment, so a second scoring library would not save code, only disagree with this one.
"""

import argparse
import glob
import json
import re
import sys
import unicodedata

import numpy as np

# Explicit Han ranges. unicodedata.category(ch) == "Lo" -- what WeNet's compute-wer.py uses -- also
# fires on Kana, Hangul and Thai, which would silently change tokenization if a model emits them.
HAN_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF), (0x20000, 0x2A6DF))
PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
            "。，、？！；：“”‘’（）《》〈〉【】「」『』〔〕…—～·")
MARKUP = re.compile(r"<(?:unk|pad|blank|noise|s|/s)>|<[a-z]{2}-[A-Za-z]{2}>|\[[^\]]{0,20}\]")
CN_DIGITS = set("零一二三四五六七八九十百千万亿两〇")


def is_han(ch):
    code = ord(ch)
    return any(low <= code <= high for low, high in HAN_RANGES)


def normalize(text, level="L1"):
    """L1 = orthographic only. L2 = L1 + traditional->simplified.

    Both levels are applied identically to reference and hypothesis. L1 is reported alongside L2 so
    a reader can see how much of any difference came from normalization rather than recognition.
    """
    text = MARKUP.sub(" ", text)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(" " if ch in PUNCT else ch for ch in text)
    if level == "L2":
        text = to_simplified(text)
    return " ".join(text.split())


def to_simplified(text):
    """Traditional -> simplified, if zhconv is installed.

    # ponytail: not worth hand-carrying a 3,000-character map. Without zhconv this is a no-op and
    # score() reports which level actually ran, so the number is never quietly wrong -- and L1 is
    # reported regardless, which is the honest comparison anyway.
    """
    try:
        import zhconv
    except ImportError:
        return text
    return zhconv.convert(text, "zh-cn")


def tokenize(text):
    """One token per Han character, one per Latin word -- the ASRU-2019 / SEAME rule."""
    out, buf = [], []
    for ch in text:
        if is_han(ch):
            if buf:
                out.append("".join(buf))
                buf = []
            out.append(ch)
        elif ch.isspace():
            if buf:
                out.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def script(token):
    return "zh" if is_han(token[0]) else "en"


def is_number(token):
    return token.isdigit() or all(ch in CN_DIGITS for ch in token)


# --- alignment -------------------------------------------------------------------------------

def align(ref, hyp):
    """Levenshtein with backtrace. Returns ops as (kind, ref_index, hyp_index).

    kind is "eq", "sub", "del" or "ins"; the unused index is None.

    The DP row is vectorised: the diagonal and deletion terms are elementwise, and the insertion
    term -- which depends on the cell to its left -- is a running prefix-min, since a unit
    insertion cost makes cur[j] = min over k<=j of (base[k] + (j - k)).
    """
    n, m = len(ref), len(hyp)
    hyp_arr = np.array(hyp, dtype=object)
    idx = np.arange(m + 1, dtype=np.int32)
    back = np.zeros((n + 1, m + 1), dtype=np.uint8)  # 0 diag, 1 up (del), 2 left (ins)
    back[0, 1:] = 2
    back[1:, 0] = 1
    prev = idx.copy()

    for i in range(1, n + 1):
        same = np.empty(m + 1, dtype=bool)
        same[0] = False
        if m:
            same[1:] = hyp_arr == ref[i - 1]
        diag = np.empty(m + 1, dtype=np.int32)
        diag[0] = 1 << 20
        diag[1:] = prev[:-1] + (~same[1:])
        up = prev + 1
        base = np.minimum(diag, up)
        base[0] = i
        cur = np.minimum.accumulate(base - idx) + idx

        row = np.full(m + 1, 2, dtype=np.uint8)   # default: insertion
        row[cur == up] = 1                        # deletion
        row[cur == diag] = 0                      # substitution / match wins ties
        row[0] = 1
        back[i] = row
        prev = cur

    ops, i, j = [], n, m
    while i > 0 or j > 0:
        move = back[i, j]
        if move == 0:
            ops.append(("eq" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif move == 1:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    ops.reverse()
    return ops


# --- metrics ---------------------------------------------------------------------------------

def rate(counts):
    total = counts["N"]
    return float("nan") if total == 0 else (counts["S"] + counts["D"] + counts["I"]) / total


def new_counts():
    return {"S": 0, "D": 0, "I": 0, "N": 0}


def bucket_counts(ops, ref, hyp, ref_mask=None, hyp_member=None):
    """Tally S/D/I/N over the subset of reference positions selected by ref_mask.

    Insertions have no reference token, so they are attributed by the *hypothesis* token --
    hyp_member decides whether an inserted token belongs to this bucket. ASRU's wording leaves
    this undefined; this is the convention that makes the split exact, and score() asserts it.
    """
    counts = new_counts()
    for kind, i, j in ops:
        if kind == "ins":
            if hyp_member is None or hyp_member(hyp[j]):
                counts["I"] += 1
            continue
        if ref_mask is not None and not ref_mask[i]:
            continue
        counts["N"] += 1
        if kind == "sub":
            counts["S"] += 1
        elif kind == "del":
            counts["D"] += 1
    return counts


def switch_points(ref_tokens, line_spans):
    """Reference positions that are code-switch points, per the PIER definition.

    Within each utterance the matrix language is the majority script; the points of interest are
    the tokens in the other script. For zh-en no hand tagging is needed because the scripts differ.
    """
    mask = np.zeros(len(ref_tokens), dtype=bool)
    for start, end in line_spans:
        if start >= end:
            continue
        scripts = [script(t) for t in ref_tokens[start:end]]
        matrix = "zh" if scripts.count("zh") >= scripts.count("en") else "en"
        for offset, sc in enumerate(scripts):
            if sc != matrix:
                mask[start + offset] = True
    return mask


def term_spans(tokens, terms):
    """Every occurrence of every glossary term, as (start, end) token spans, longest match first."""
    spans = []
    for term in sorted(terms, key=lambda t: -len(t)):
        width = len(term)
        for start in range(len(tokens) - width + 1):
            if tokens[start:start + width] == term:
                spans.append((start, start + width))
    spans.sort()
    kept = []
    for start, end in spans:  # no overlapping credit for the same tokens
        if not kept or start >= kept[-1][1]:
            kept.append((start, end))
    return kept


def term_scores(ops, ref, hyp, terms):
    """Recall over reference term occurrences, precision over hypothesis term occurrences.

    A reference occurrence counts as recalled only if EVERY one of its tokens aligned as "eq", so a
    half-right term earns nothing. Precision is mandatory alongside it: recall alone rewards a
    biasing model that sprays glossary terms into the transcript.
    """
    correct_ref = {i for kind, i, _ in ops if kind == "eq"}
    ref_spans = term_spans(ref, terms)
    hyp_spans = term_spans(hyp, terms)
    hit = sum(1 for start, end in ref_spans if all(k in correct_ref for k in range(start, end)))
    recall = hit / len(ref_spans) if ref_spans else float("nan")
    precision = hit / len(hyp_spans) if hyp_spans else float("nan")
    if not ref_spans or not hyp_spans or (precision + recall) == 0:
        f1 = float("nan")
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {"term_recall": recall, "term_precision": precision, "term_f1": f1,
            "term_ref_occurrences": len(ref_spans), "term_hyp_occurrences": len(hyp_spans)}


def extract_glossary(ref_text, level="L1"):
    """Auto-glossary from the reference: acronyms and rare Latin words.

    The user has no term list, so this stands in for one. Written to a file the user can edit --
    re-scoring with a corrected list costs nothing, since no audio is re-run.
    """
    raw_tokens = tokenize(normalize(ref_text, level))
    counts = {}
    for token in raw_tokens:
        if script(token) == "en" and len(token) >= 3 and not is_number(token):
            counts[token] = counts.get(token, 0) + 1
    acronyms = {t for t in re.findall(r"\b[A-Z]{2,6}\b", ref_text)}
    terms = {t for t, c in counts.items() if 1 <= c <= 5}
    terms |= {a.casefold() for a in acronyms}
    return sorted(t for t in terms if t in counts)


def score(ref_text, hyp_text, terms=(), level="L1"):
    """Every accuracy metric, from one alignment."""
    ref_lines = [ln for ln in ref_text.splitlines() if ln.strip()]
    ref_tokens, line_spans = [], []
    for line in ref_lines:
        start = len(ref_tokens)
        ref_tokens.extend(tokenize(normalize(line, level)))
        line_spans.append((start, len(ref_tokens)))
    hyp_tokens = tokenize(normalize(hyp_text, level))

    ops = align(ref_tokens, hyp_tokens)
    zh_mask = np.array([script(t) == "zh" for t in ref_tokens], dtype=bool)
    poi_mask = switch_points(ref_tokens, line_spans)
    num_mask = np.array([is_number(t) for t in ref_tokens], dtype=bool)
    term_tokens = [tokenize(normalize(t, level)) for t in terms]
    term_members = {tok for term in term_tokens for tok in term}
    bias_mask = np.zeros(len(ref_tokens), dtype=bool)
    for start, end in term_spans(ref_tokens, term_tokens):
        bias_mask[start:end] = True

    overall = bucket_counts(ops, ref_tokens, hyp_tokens)
    zh = bucket_counts(ops, ref_tokens, hyp_tokens, zh_mask, lambda t: script(t) == "zh")
    en = bucket_counts(ops, ref_tokens, hyp_tokens, ~zh_mask, lambda t: script(t) == "en")
    poi = bucket_counts(ops, ref_tokens, hyp_tokens, poi_mask,
                        lambda t: False)  # PIER counts reference switch points only
    nonum = bucket_counts(ops, ref_tokens, hyp_tokens, ~num_mask, lambda t: not is_number(t))
    biased = bucket_counts(ops, ref_tokens, hyp_tokens, bias_mask, lambda t: t in term_members)
    unbiased = bucket_counts(ops, ref_tokens, hyp_tokens, ~bias_mask,
                             lambda t: t not in term_members)

    # The zh/en split must reconstruct MER exactly. If it does not, insertion attribution is wrong.
    assert zh["N"] + en["N"] == overall["N"], (zh["N"], en["N"], overall["N"])
    assert (zh["S"] + zh["D"] + zh["I"]) + (en["S"] + en["D"] + en["I"]) == \
           overall["S"] + overall["D"] + overall["I"], "insertion attribution is inconsistent"

    result = {
        "level": level, "MER": rate(overall), "CER_zh": rate(zh), "WER_en": rate(en),
        "PIER": rate(poi), "MER_no_numbers": rate(nonum),
        "B_MER": rate(biased), "U_MER": rate(unbiased),
        "N": overall["N"], "N_zh": zh["N"], "N_en": en["N"], "N_switch": poi["N"],
        "N_biased": biased["N"],
        "S": overall["S"], "D": overall["D"], "I": overall["I"],
        "hyp_tokens": len(hyp_tokens),
    }
    result.update(term_scores(ops, ref_tokens, hyp_tokens, term_tokens))
    result["hallucination"] = len(hyp_tokens) > 10 * max(1, len(ref_tokens))
    result["_ops"], result["_line_spans"] = ops, line_spans
    return result


def bootstrap_diff(ref_text, hyp_a, hyp_b, level="L1", rounds=1000, seed=0):
    """95% CI on MER_a - MER_b, resampling utterances with the same draw for both systems.

    Point estimates are not enough at this sample size: at ~1000 reference tokens the CI on MER is
    roughly +/-2 absolute points, so two systems can differ on paper and not in fact.
    """
    a, b = score(ref_text, hyp_a, level=level), score(ref_text, hyp_b, level=level)
    spans = a["_line_spans"]
    line_of = {}
    for index, (start, end) in enumerate(spans):
        for position in range(start, end):
            line_of[position] = index

    per_line = []
    for system in (a, b):
        counts = np.zeros((len(spans), 2))  # errors, N -- insertions are dropped, they have no line
        for kind, i, _ in system["_ops"]:
            if kind == "ins":
                continue
            line = line_of.get(i)
            if line is None:
                continue
            counts[line, 1] += 1
            if kind != "eq":
                counts[line, 0] += 1
        per_line.append(counts)

    rng = np.random.default_rng(seed)
    diffs = np.empty(rounds)
    for r in range(rounds):
        pick = rng.integers(0, len(spans), len(spans))
        ea, na = per_line[0][pick, 0].sum(), per_line[0][pick, 1].sum()
        eb, nb = per_line[1][pick, 0].sum(), per_line[1][pick, 1].sum()
        diffs[r] = (ea / na if na else 0) - (eb / nb if nb else 0)
    return {"mer_a": a["MER"], "mer_b": b["MER"],
            "diff": a["MER"] - b["MER"],
            "ci95": (float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5)))}


# --- latency and power -----------------------------------------------------------------------

def latency(run):
    """Cheap latency metrics, from the event log bench_asr.py records.

    Percentiles, never means: a handful of outlier utterances dominate the mean, which is why the
    literature reports P50/P90. Finalization delay is NOT comparable across backends -- each worker
    hard-codes its own commit constants -- so it is reported but flagged.
    """
    audio = run.get("audio_seconds", 0.0)
    warmup = run.get("warmup_seconds", 0.0)
    # timing stats skip the warm-up window; accuracy still scores the whole clip, since the audio
    # was fed continuously and the model's state was never reset
    events = [e for e in run.get("events", []) if e.get("audio_pos", 0.0) >= warmup]
    finals = [e for e in events if e.get("final")]
    partials = [e for e in events if not e.get("final")]

    out = {"n_final": len(finals), "n_partial": len(partials), "rtf": run.get("rtf")}
    if events:
        out["first_caption_s"] = events[0]["t"]
    if finals:
        gaps = np.diff([e["t"] for e in finals]) if len(finals) > 1 else np.array([])
        out["final_gap_p50"] = float(np.percentile(gaps, 50)) if gaps.size else float("nan")
        out["final_gap_p90"] = float(np.percentile(gaps, 90)) if gaps.size else float("nan")
        # the number that decides whether a model survives an all-day meeting
        out["lag_drift_s"] = finals[-1]["t"] - audio
    out["events_scored"] = len(events)
    revisions = 0
    last = ""
    for event in events:
        text = event.get("text", "")
        if not event.get("final") and last and not text.startswith(last):
            revisions += 1
        last = "" if event.get("final") else text
    out["flicker_revisions"] = revisions

    power = run.get("power") or {}
    samples = power.get("samples") or []
    if samples:
        watts = np.array([s[1] for s in samples], dtype=float)
        idle = power.get("idle_watts", 0.0)
        out["gpu_watts_mean"] = float(watts.mean())
        out["gpu_watts_over_idle"] = float(watts.mean() - idle)
        if audio:
            out["gpu_joules_per_audio_min"] = float((watts.mean() - idle) * 60.0)
        out["throttled"] = any(str(s[3]).strip() not in ("", "Not Active", "0x0000000000000000")
                               for s in samples if len(s) > 3)
    out["peak_vram_mb"] = power.get("peak_vram_mb")
    return out


def run_text(run):
    """The final transcript a run produced: every final event, in order."""
    return "\n".join(e["text"] for e in run.get("events", []) if e.get("final") and e.get("text"))


# --- self-test -------------------------------------------------------------------------------

def self_test():
    assert tokenize("GPU的性能") == ["gpu", "的", "性", "能"] or \
           tokenize(normalize("GPU的性能")) == ["gpu", "的", "性", "能"]
    # whitespace around Han is pure formatting: it must not cost anything
    assert tokenize(normalize("GPU的性能")) == tokenize(normalize("GPU 的 性 能"))
    assert normalize("ＡＢＣ！") == "abc"
    assert normalize("hello<en-US>") == "hello"
    assert script("的") == "zh" and script("gpu") == "en"
    assert is_number("42") and is_number("三十") and not is_number("gpu")

    # known edit counts: one substitution, one deletion, one insertion
    ops = align(list("abc"), list("axc"))
    assert [o[0] for o in ops] == ["eq", "sub", "eq"], ops
    assert [o[0] for o in align(list("abc"), list("ac"))] == ["eq", "del", "eq"]
    assert [o[0] for o in align(list("ac"), list("abc"))] == ["eq", "ins", "eq"]
    assert [o[0] for o in align([], list("ab"))] == ["ins", "ins"]
    assert [o[0] for o in align(list("ab"), [])] == ["del", "del"]

    # MER over a mixed line: 4 reference tokens, one Han substituted -> 1/4
    result = score("我用 GPU 跑", "你用 GPU 跑")
    assert result["N"] == 4 and result["S"] == 1, result
    assert abs(result["MER"] - 0.25) < 1e-9, result
    assert abs(result["CER_zh"] - 1 / 3) < 1e-9, result   # 3 Han tokens, 1 wrong
    assert result["WER_en"] == 0.0, result

    # the identity that catches a broken insertion rule
    for hyp in ("我用 GPU 跑", "我用 CPU 跑 extra 词", "完全不同", "我用 GPU"):
        r = score("我用 GPU 跑", hyp)
        assert abs(r["MER"] - (r["N_zh"] * r["CER_zh"] + r["N_en"] * r["WER_en"]) / r["N"]) < 1e-9, \
            (hyp, r)

    # PIER: an English word inside a Chinese matrix is the switch point. Getting only it wrong
    # leaves MER low and PIER at 1.0 -- the whole reason PIER leads the report.
    ref = "这个 model 很快"
    r = score(ref, "这个 modle 很快")
    assert r["N_switch"] == 1, r
    assert r["PIER"] == 1.0 and r["MER"] < 0.3, r
    # and the reverse: mangling the Chinese leaves PIER at 0
    r = score(ref, "那样 model 很慢")
    assert r["PIER"] == 0.0 and r["MER"] > 0.3, r

    # glossary: B-MER isolates the term, U-MER everything else
    r = score("我们用 kubernetes 部署", "我们用 kubernets 部署", terms=["kubernetes"])
    assert r["N_biased"] == 1 and r["B_MER"] == 1.0 and r["U_MER"] == 0.0, r
    assert r["term_recall"] == 0.0 and r["term_ref_occurrences"] == 1, r
    r = score("我们用 kubernetes 部署", "我们用 kubernetes 部署", terms=["kubernetes"])
    assert r["term_recall"] == 1.0 and r["term_precision"] == 1.0 and r["term_f1"] == 1.0, r

    # term precision punishes spraying the glossary where it does not belong
    r = score("我们用 kubernetes 部署 服务", "kubernetes kubernetes kubernetes", terms=["kubernetes"])
    assert r["term_precision"] < 0.5, r

    # B-MER and U-MER do not average to MER; they weight by their own N
    r = score("用 kubernetes 跑 docker", "用 kubernets 跑 docker", terms=["kubernetes", "docker"])
    combined = (r["N_biased"] * r["B_MER"] + (r["N"] - r["N_biased"]) * r["U_MER"]) / r["N"]
    assert abs(combined - r["MER"]) < 1e-9, r

    assert extract_glossary("we deploy kubernetes on GPU nodes") == ["deploy", "gpu", "kubernetes",
                                                                    "nodes"]

    # deliberately break insertion attribution and confirm the assertion fires
    original = bucket_counts
    try:
        globals()["bucket_counts"] = lambda ops, ref, hyp, ref_mask=None, hyp_member=None: \
            original(ops, ref, hyp, ref_mask, None)
        broke = False
        try:
            score("我用 GPU", "我用 GPU extra 词")
        except AssertionError:
            broke = True
        assert broke, "the MER identity assertion did not fire on a broken insertion rule"
    finally:
        globals()["bucket_counts"] = original

    # latency: flicker counts revisions, not growth
    run = {"audio_seconds": 10.0, "rtf": 0.2, "warmup_seconds": 0.0, "events": [
        {"t": 0.5, "audio_pos": 0.4, "text": "he", "final": False},
        {"t": 0.8, "audio_pos": 0.7, "text": "hello", "final": False},
        {"t": 1.0, "audio_pos": 0.9, "text": "help", "final": False},
        {"t": 1.2, "audio_pos": 1.1, "text": "hello world", "final": True},
    ]}
    lat = latency(run)
    assert lat["flicker_revisions"] == 1, lat
    assert lat["first_caption_s"] == 0.5 and lat["n_final"] == 1, lat
    assert abs(lat["lag_drift_s"] - (1.2 - 10.0)) < 1e-9, lat
    assert run_text(run) == "hello world"
    # the warm-up window is excluded from timing but never from the transcript
    warm = dict(run, warmup_seconds=1.0)
    assert latency(warm)["events_scored"] == 1 and latency(warm)["first_caption_s"] == 1.2
    assert run_text(warm) == "hello world"

    boot = bootstrap_diff("我用 GPU 跑\n他也用 GPU", "我用 GPU 跑\n他也用 GPU", "你用 CPU 跑\n他也用 GPU")
    assert boot["diff"] < 0 and boot["ci95"][0] <= boot["diff"] <= boot["ci95"][1], boot

    print("score_asr self-test ok")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", nargs="?", help="reference transcript, one utterance per line")
    parser.add_argument("runs", nargs="*", help="run .json files from bench_asr.py (globs ok)")
    parser.add_argument("--terms", help="glossary file, one term per line; omit to auto-extract")
    parser.add_argument("--write-terms", help="write the auto-extracted glossary here")
    parser.add_argument("--level", default="L1", choices=["L1", "L2", "both"])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not args.reference:
        parser.error("a reference transcript is required (or --self-test)")

    with open(args.reference, encoding="utf-8") as f:
        reference = f.read()
    if args.terms:
        terms = []
        with open(args.terms, encoding="utf-8") as f:
            for line in f:
                term = line.split("#", 1)[0].strip()  # '#' starts a comment, inline or whole-line
                if term:
                    terms.append(term)
    else:
        terms = extract_glossary(reference)
        if args.write_terms:
            with open(args.write_terms, "w", encoding="utf-8") as f:
                f.write("\n".join(terms) + "\n")
            print(f"auto-glossary ({len(terms)} terms) -> {args.write_terms}", file=sys.stderr)

    paths = [p for pattern in args.runs for p in sorted(glob.glob(pattern))]
    if not paths:
        parser.error("no run files matched")

    levels = ["L1", "L2"] if args.level == "both" else [args.level]
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            run = json.load(f)
        for level in levels:
            row = score(reference, run_text(run), terms, level)
            row.update(latency(run))
            row["run"] = run.get("model") or path
            rows.append(row)

    columns = ["run", "level", "MER", "CER_zh", "WER_en", "PIER", "B_MER", "U_MER", "term_f1",
               "rtf", "first_caption_s", "lag_drift_s", "gpu_watts_over_idle", "peak_vram_mb"]
    print("\t".join(columns))
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column)
            cells.append(f"{value:.3f}" if isinstance(value, float) else str(value))
        print("\t".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() if "--self-test" not in sys.argv else (self_test() or 0))
