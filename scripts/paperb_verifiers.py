#!/usr/bin/env python3
"""
scripts/paperb_verifiers.py
Paper B's ModernBERT verifier (docs/research/detect_then_fallback_study.md v2, §5), in rotating rounds
(paperb_folds.rounds): round r trains on three folds, and scores every distinct candidate of its
calibration fold (r + 1 mod 5) and of its test fold r. So each question gets one test score, from a model
that saw neither it nor its calibration fold, and each round's thresholds come from its own calibration
scores (paperb_analyze.py).

Model:    answerdotai/ModernBERT-base, full fine-tune (version 1's hyperparameters).
          (qwen = Qwen/Qwen3-0.6B with a LoRA head is kept in the code but dropped from v2.)
Regimes:  real      = the training questions' pool candidates (docs/eval/paperb_pool.json)
          mutation  = the training questions' reference SQL and its mutants (paperb_mutations.json)

Input text (identical for every verifier, Laya included): question, SQL, and the result text (row count,
columns, first rows, or "failed to run"). Training examples are deduplicated by (question, SQL).
Run with the research venv (torch, transformers, peft):

    .venv-reranker/bin/python scripts/paperb_verifiers.py --model modernbert --regime real
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / "docs" / "eval"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paperb_folds import SEED, folds, rounds  # noqa: E402,F401 - folds re-exported for older callers

MAX_LEN = 512                                 # noqa: literals - pre-registered input cap
ACCUM = 2                                     # noqa: literals - micro-batches per batch (GPU memory; the batch size is unchanged)
SCORE_BATCH = 16                              # noqa: literals - scoring batch
HF = {"modernbert": "answerdotai/ModernBERT-base", "qwen": "Qwen/Qwen3-0.6B"}
# Fixed before any verifier was trained (not tuned on test folds).
HP = {"modernbert": {"lr": 3e-5, "epochs": 3, "batch": 16},
      "qwen": {"lr": 2e-4, "epochs": 2, "batch": 8, "lora_r": 16, "lora_alpha": 32, "lora_dropout": 0.05}}


def result_text(sql: str | None, preview: dict | None) -> str:
    if preview is None:
        return "failed to run" if sql else "no SQL"
    rows = " | ".join(", ".join(r) for r in preview["rows"]) or "(no rows)"
    return f"{preview['row_count']} rows; columns: {', '.join(preview['columns'])}; first rows: {rows}"


def render(question: str, sql: str | None, preview: dict | None) -> str:
    return f"Question: {question}\nSQL: {sql or '(none)'}\nResult: {result_text(sql, preview)}"


def training_examples(regime: str, questions: dict[int, dict], train_ids: set[int]) -> list[tuple[str, int]]:
    seen: set[tuple[int, str]] = set()
    out = []
    if regime == "real":
        for qid in sorted(train_ids):
            q = questions[qid]
            for c in q["candidates"]:
                key = (qid, c["sql"] or "")
                if key not in seen:
                    seen.add(key)
                    out.append((render(q["question"], c["sql"], c["preview"]), int(c["correct"])))
    else:
        mut = {m["question_id"]: m for m in json.loads((EVAL / "paperb_mutations.json").read_text())["questions"]}
        for qid in sorted(train_ids):
            for e in mut[qid]["examples"]:
                key = (qid, e["sql"])
                if key not in seen:
                    seen.add(key)
                    out.append((render(questions[qid]["question"], e["sql"], e["preview"]), int(e["correct"])))
    return out


def build(model_name: str, device: str):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(HF[model_name])
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForSequenceClassification.from_pretrained(HF[model_name], num_labels=2)
    model.config.pad_token_id = tok.pad_token_id
    if model_name == "qwen":
        from peft import LoraConfig, TaskType, get_peft_model
        hp = HP["qwen"]
        model = get_peft_model(model, LoraConfig(task_type=TaskType.SEQ_CLS, r=hp["lora_r"], lora_alpha=hp["lora_alpha"],
                                                 lora_dropout=hp["lora_dropout"],
                                                 target_modules=["q_proj", "k_proj", "v_proj", "o_proj"]))
    return tok, model.to(device)


def batches(items: list, size: int, shuffle: bool, rng: random.Random):
    order = list(range(len(items)))
    if shuffle:
        rng.shuffle(order)
    for i in range(0, len(order), size):
        yield [items[j] for j in order[i:i + size]]


def train_and_score(model_name: str, train: list[tuple[str, int]], score_texts: list[str],
                    device: str) -> tuple[list[float], float]:
    """Train on `train`, then P(correct) for each of `score_texts`, and milliseconds per check (batched)."""
    import torch
    torch.manual_seed(SEED)
    rng = random.Random(SEED)
    hp = HP[model_name]
    tok, model = build(model_name, device)
    if hasattr(model, "gradient_checkpointing_enable"):
        model.gradient_checkpointing_enable()     # activations recomputed in backward: the Mac's GPU memory
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=hp["lr"], weight_decay=0.01)
    steps = hp["epochs"] * math.ceil(len(train) / hp["batch"])
    warm = max(1, steps // 10)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / warm) * max(0.0, (steps - s) / max(1, steps - warm)))
    micro = max(1, hp["batch"] // ACCUM)          # the batch is unchanged; it is built from ACCUM micro-batches
    model.train()
    for _ in range(hp["epochs"]):
        for batch in batches(train, hp["batch"], True, rng):
            for j in range(0, len(batch), micro):
                part = batch[j:j + micro]
                enc = tok([t for t, _ in part], truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt").to(device)
                labels = torch.tensor([y for _, y in part], device=device)
                loss = model(**enc, labels=labels).loss * len(part) / len(batch)
                loss.backward()
                del enc, labels, loss
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            if device == "mps":
                torch.mps.empty_cache()           # variable lengths otherwise grow the MPS cache (the Laya pilot)
    model.eval()
    probs: list[float] = []
    t0 = time.perf_counter()
    with torch.no_grad():
        for i in range(0, len(score_texts), SCORE_BATCH):
            enc = tok(score_texts[i:i + SCORE_BATCH], truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt").to(device)
            probs += torch.softmax(model(**enc).logits.float(), dim=-1)[:, 1].tolist()
            if device == "mps":
                torch.mps.empty_cache()
    per_check_ms = (time.perf_counter() - t0) * 1000 / max(1, len(score_texts))
    del model, opt
    if device == "mps":
        torch.mps.empty_cache()
    return probs, per_check_ms


def main() -> int:
    import torch
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", choices=sorted(HF), required=True)
    ap.add_argument("--regime", choices=["real", "mutation"], required=True)
    ap.add_argument("--max-rounds", type=int, help="stop after this many new rounds (task time limits)")
    args = ap.parse_args()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    pool = json.loads((EVAL / "paperb_pool.json").read_text())["questions"]
    questions = {q["question_id"]: q for q in pool}
    out_path = EVAL / f"paperb_scores_{args.model}_{args.regime}_rotation.json"
    record = json.loads(out_path.read_text()) if out_path.exists() else {
        "model": HF[args.model], "regime": args.regime, "scheme": "rotation", "hp": HP[args.model],
        "seed": SEED, "rounds": {}, "test": {}, "calib": {}}
    done_now = 0
    for rnd in rounds(folds(pool)):
        r = str(rnd["round"])
        if r in record["rounds"]:
            continue
        if args.max_rounds is not None and done_now >= args.max_rounds:
            break
        done_now += 1
        train = training_examples(args.regime, questions, rnd["train"])
        keys: list[tuple[str, str, str]] = []           # (role, "qid|i", text)
        texts: list[str] = []
        index: dict[str, int] = {}
        for role in ("calib", "test"):
            for qid in sorted(rnd[role]):
                q = questions[qid]
                for i, c in enumerate(q["candidates"]):
                    text = render(q["question"], c["sql"], c["preview"])
                    if text not in index:                # each distinct candidate is scored once
                        index[text] = len(texts)
                        texts.append(text)
                    keys.append((role, f"{qid}|{i}", text))
        t0 = time.monotonic()
        probs, per_check_ms = train_and_score(args.model, train, texts, device)
        record["calib"][r] = {}
        for role, key, text in keys:
            p = round(probs[index[text]], 5)
            if role == "test":
                record["test"][key] = p
            else:
                record["calib"][r][key] = p
        record["rounds"][r] = {"train_questions": len(rnd["train"]), "train_examples": len(train),
                               "train_positive": sum(y for _, y in train), "scored_texts": len(texts),
                               "ms_per_check": round(per_check_ms, 2),
                               "minutes": round((time.monotonic() - t0) / 60, 1)}
        out_path.write_text(json.dumps(record, indent=1) + "\n")
        print(f"round {r}: {record['rounds'][r]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
