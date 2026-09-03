#!/usr/bin/env python3
"""Merge several transcripts of one clip into a reference candidate plus a list of disputes.

Takes the run .json files of N independent systems, treats the first as the backbone, and marks
every backbone token that at least one other system also produced nearby. A token two of three
systems agree on is accepted; the rest are collected into a short review file, so a human reads
the disagreements instead of the whole transcript.

  python src/python/make_reference.py bench/ref/B-codeswitch \\
      bench/runs/api__gpt-4o-transcribe__B-codeswitch__max.json \\
      bench/refruns/sherpa__...fire-red...__B-codeswitch__max.json \\
      bench/refruns/sherpa__...sense-voice...__B-codeswitch__max.json

Writes <out>.txt (edit this into the final reference) and <out>.disputes.md (what to look at).

# ponytail: pairwise alignments against one backbone, not a real N-way multiple alignment. With
# three systems the majority rule only needs "did anyone else also say this token", which a
# backbone comparison answers exactly -- an N-way aligner would be a lot of code for the same
# answer, and the human is reading the disputed spans either way.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_asr import align, normalize, tokenize  # noqa: E402

SENTENCE_END = re.compile(r"(?<=[。．！？!?])\s*|(?<=[.])\s+")


def run_text(path):
    with open(path, encoding="utf-8") as f:
        run = json.load(f)
    text = " ".join(e["text"] for e in run.get("events", []) if e.get("final") and e.get("text"))
    return run.get("label") or os.path.basename(path), text


def segment(text, max_chars=90):
    """One utterance per line: the resampling unit for the bootstrap and a readable diff unit."""
    lines = []
    for piece in SENTENCE_END.split(text):
        piece = (piece or "").strip()
        while len(piece) > max_chars:            # a run-on with no punctuation still has to break
            cut = piece.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            lines.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            lines.append(piece)
    return [ln for ln in lines if ln]


def tokenize_utterances(utterances):
    """Flat token list plus (start, end) span per utterance, over that same flat list."""
    tokens, spans = [], []
    for utterance in utterances:
        start = len(tokens)
        tokens.extend(tokenize(normalize(utterance)))
        spans.append((start, len(tokens)))
    return tokens, spans


def support(backbone_tokens, spans, other_tokens):
    """Which backbone positions this system also produced, aligned utterance by utterance.

    Aligning the whole 10-minute document against the whole other transcript in one DP call is
    provably correct for MER-style aggregate metrics (the total edit count is the true minimum no
    matter which tied-optimal path is taken -- score_asr.py relies on exactly this), but a meeting
    transcript is full of repeated function words and stock phrases ("然后", "就是", "past oracle"
    said forty times), so an equal-cost path can legally match a token against a distant duplicate
    instead of its real local counterpart -- harmless for a sum, useless for a human deciding what
    one voter actually corroborated.

    Restricting each utterance to a window of the other transcript at roughly the same fractional
    position removes the distant-duplicate degrees of freedom without changing the alignment
    algorithm itself.
    """
    agreed = set()
    other_len = len(other_tokens)
    backbone_len = len(backbone_tokens)
    for start, end in spans:
        width = end - start
        if width == 0:
            continue
        center = (start + width / 2) / max(1, backbone_len) * other_len
        margin = max(80, 4 * width)   # generous: a slow or fast voter can drift a lot over 10 min
        lo, hi = max(0, int(center - margin)), min(other_len, int(center + margin))
        for kind, i, _ in align(backbone_tokens[start:end], other_tokens[lo:hi]):
            if kind == "eq":
                agreed.add(start + i)
    return agreed


def main():
    p = argparse.ArgumentParser()
    p.add_argument("out_stem")
    p.add_argument("runs", nargs="+", help="backbone first, then the other voters")
    p.add_argument("--context", type=int, default=6, help="tokens of context around a dispute")
    args = p.parse_args()
    if len(args.runs) < 2:
        p.error("need at least two systems to find a disagreement")

    names, texts = zip(*[run_text(path) for path in args.runs])
    utterances = segment(texts[0])
    backbone, spans = tokenize_utterances(utterances)
    votes = [support(backbone, spans, tokenize(normalize(t))) for t in texts[1:]]
    agreed_by = [sum(1 for v in votes if i in v) for i in range(len(backbone))]
    disputed_set = {i for i, n in enumerate(agreed_by) if n == 0}

    # group neighbouring disputed tokens into spans, so one garbled phrase is one review item
    review, start = [], None
    for i in range(len(backbone) + 1):
        if i in disputed_set:
            start = i if start is None else start
        elif start is not None:
            review.append((start, i))
            start = None

    os.makedirs(os.path.dirname(os.path.abspath(args.out_stem)), exist_ok=True)
    with open(args.out_stem + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(utterances) + "\n")

    with open(args.out_stem + ".disputes.md", "w", encoding="utf-8") as f:
        f.write(f"# Disputes for `{os.path.basename(args.out_stem)}`\n\n")
        f.write(f"Backbone: **{names[0]}**. Other voters: {', '.join(names[1:])}.\n\n")
        f.write(f"{len(backbone)} backbone tokens, {len(backbone) - len(disputed_set)} confirmed by "
                f"at least one other system nearby "
                f"({100 * (1 - len(disputed_set) / max(1, len(backbone))):.1f}%), "
                f"**{len(review)} disputed spans** below.\n\n")
        f.write("Each span is text only this system produced (the other two said something "
                "different in roughly the same place, or nothing). Fix it in the `.txt` if it is "
                "wrong; leave it if it is right. Most will be right -- the other two voters are "
                "weaker models, so a lone-backbone span often just means they missed it or "
                "garbled a term worse.\n\n")
        for number, (begin, end) in enumerate(review, 1):
            left = " ".join(backbone[max(0, begin - args.context):begin])
            middle = " ".join(backbone[begin:end])
            right = " ".join(backbone[end:end + args.context])
            f.write(f"{number}. …{left} **[{middle}]** {right}…\n")

    print(f"{args.out_stem}.txt          reference candidate ({len(backbone)} tokens, "
          f"{len(utterances)} utterances)")
    print(f"{args.out_stem}.disputes.md  {len(review)} spans to review "
          f"({len(disputed_set)} of {len(backbone)} tokens unconfirmed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
