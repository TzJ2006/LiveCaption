#!/usr/bin/env python3
"""Score every run in bench/runs against the reference(s) and write bench/report.md.

  python src/python/write_report.py bench/ref/B-codeswitch.txt=B-codeswitch \\
                                     bench/ref/A-english.txt=A-english \\
                                     --terms bench/terms.txt --out bench/report.md
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_asr import score, latency, run_text, bootstrap_diff, extract_glossary  # noqa: E402
import json  # noqa: E402


def load_terms(path):
    terms = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            term = line.split("#", 1)[0].strip()
            if term:
                terms.append(term)
    return terms


def fmt(x, pct=False):
    if x is None or (isinstance(x, float) and x != x):  # NaN
        return "-"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return f"{x*100:.1f}%" if pct else f"{x:.3f}"
    return str(x)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("clips", nargs="+", help="ref.txt=clip-name pairs")
    p.add_argument("--terms", default="")
    p.add_argument("--runs-dir", default="bench/runs")
    p.add_argument("--out", default="bench/report.md")
    p.add_argument("--reference-status", default="UNREVIEWED CANDIDATE",
                   help="stamped at the top of the report so a stale reference is never mistaken "
                        "for a final one")
    args = p.parse_args()

    with open(args.out, "w", encoding="utf-8") as out:
        out.write("# LiveCaption ASR Benchmark Report\n\n")
        out.write(f"**Reference status: {args.reference_status}.** Numbers below will change once "
                   "the disputed spans in `bench/ref/*.disputes.md` are reviewed -- treat relative "
                   "ranking as more solid than absolute MER at this stage.\n\n")

        for pair in args.clips:
            ref_path, clip = pair.split("=", 1)
            with open(ref_path, encoding="utf-8") as f:
                reference = f.read()
            terms = load_terms(args.terms) if args.terms else extract_glossary(reference)

            pattern = os.path.join(args.runs_dir, f"*__{clip}__*.json")
            paths = sorted(glob.glob(pattern))
            if not paths:
                out.write(f"## {clip}\n\nno runs found matching `{pattern}`\n\n")
                continue

            rows, hyp_text_by_label = [], {}
            for path in paths:
                with open(path, encoding="utf-8") as f:
                    run = json.load(f)
                text = run_text(run)
                row = score(reference, text, terms, "L1")
                row.update(latency(run))
                row["label"] = run.get("label") or os.path.basename(path)
                row["pace"] = run.get("pace", "?")
                rows.append(row)
                hyp_text_by_label[row["label"]] = text  # any pace's text; content is pace-invariant

            out.write(f"## {clip}\n\n")
            out.write(f"{rows[0]['N']} reference tokens ({rows[0]['N_zh']} zh / {rows[0]['N_en']} "
                      f"en), {rows[0]['N_switch']} code-switch points, "
                      f"{sum(1 for _ in terms)} glossary terms.\n\n")

            out.write("### Accuracy (PIER leads -- it is the code-switching number)\n\n")
            out.write("| Model | MER | CER_zh | WER_en | **PIER** | B-MER | U-MER | Term F1 | Hallucination |\n")
            out.write("|---|---|---|---|---|---|---|---|---|\n")
            seen = set()
            for row in rows:
                if row["label"] in seen:  # realtime/max pace give identical accuracy; print once
                    continue
                seen.add(row["label"])
                out.write(f"| {row['label']} | {fmt(row['MER'], True)} | {fmt(row['CER_zh'], True)} "
                          f"| {fmt(row['WER_en'], True)} | **{fmt(row['PIER'], True)}** | "
                          f"{fmt(row['B_MER'], True)} | {fmt(row['U_MER'], True)} | "
                          f"{fmt(row['term_f1'])} | {fmt(row['hallucination'])} |\n")

            out.write("\n### Latency and power (`realtime` pace only -- `max` measures throughput, "
                       "not latency)\n\n")
            out.write("| Model | RTF (max pace) | First caption (s) | Final gap P50/P90 (s) | "
                       "Lag drift (s) | Flicker | GPU W over idle |\n")
            out.write("|---|---|---|---|---|---|---|\n")
            rtf_by_label = {r["label"]: r["rtf"] for r in rows if r["pace"] == "max"}
            for row in rows:
                if row["pace"] != "realtime":
                    continue
                out.write(f"| {row['label']} | {fmt(rtf_by_label.get(row['label']))} | "
                          f"{fmt(row.get('first_caption_s'))} | "
                          f"{fmt(row.get('final_gap_p50'))} / {fmt(row.get('final_gap_p90'))} | "
                          f"{fmt(row.get('lag_drift_s'))} | {row.get('flicker_revisions', '-')} | "
                          f"{fmt(row.get('gpu_watts_over_idle'))} |\n")

            # bootstrap the gap between the two best-MER systems: is it a real difference or noise?
            by_mer = sorted({r["label"]: r["MER"] for r in rows}.items(), key=lambda kv: kv[1])
            if len(by_mer) >= 2:
                a_label, b_label = by_mer[0][0], by_mer[1][0]
                boot = bootstrap_diff(reference, hyp_text_by_label[a_label],
                                      hyp_text_by_label[b_label])
                lo, hi = boot["ci95"]
                crosses_zero = lo <= 0 <= hi
                out.write(f"\nClosest pair by MER: **{a_label}** ({fmt(by_mer[0][1], True)}) vs "
                          f"**{b_label}** ({fmt(by_mer[1][1], True)}). Bootstrap 95% CI on the "
                          f"difference: [{fmt(lo, True)}, {fmt(hi, True)}] -- "
                          f"{'not' if crosses_zero else 'IS'} statistically distinguishable at "
                          f"this sample size.\n")
            out.write("\n")

    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
