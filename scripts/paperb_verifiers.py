#!/usr/bin/env python3
"""
scripts/paperb_verifiers.py
Paper B's fine-tuned verifiers (docs/research/detect_then_fallback_study.md), scored out-of-fold: the
393 held-out questions are split into 5 folds stratified by database (seed 20261005), and every pool
candidate gets P(correct) from a model trained only on the other folds' questions ("setup-time"
training on other reference questions from the same databases). --scheme lodo leaves one database out
instead (unseen schema).

Models:   modernbert = answerdotai/ModernBERT-base, full fine-tune
          qwen       = Qwen/Qwen3-0.6B with a LoRA classification head (peft)
Regimes:  real       = the pool candidates of the training questions (docs/eval/paperb_pool.json)
          mutation   = the reference SQL and its mutants for the training questions (paperb_mutations.json)

Input text (identical for every model): question, SQL, and the result preview (row count, columns,
first rows, or "failed to run"). Training examples are deduplicated by (question, SQL).
Run with the research venv (torch, transformers, peft):

    .venv-reranker/bin/python scripts/paperb_verifiers.py --model modernbert --regime real
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / "docs" / "eval"
SEED = 20261005
FOLDS = 5                                     # noqa: literals - pre-registered
MAX_LEN = 512                                 # noqa: literals - pre-registered input cap
HF = {"modernbert": "answerdotai/ModernBERT-base", "qwen": "Qwen/Qwen3-0.6B"}
# Fixed before any verifier was trained (not tuned on test folds).
HP = {"modernbert": {"lr": 3e-5, "epochs": 3, "batch": 16},
      "qwen": {"lr": 2e-4, "epochs": 2, "batch": 8, "lora_r": 16, "lora_alpha": 32, "lora_dropout": 0.05}}


def render(question: str, sql: str | None, preview: dict | None) -> str:
    if preview is None:
        result = "failed to run" if sql else "no SQL"
    else:
        rows = " | ".join(", ".join(r) for r in preview["rows"]) or "(no rows)"
        result = f"{preview['row_count']} rows; columns: {', '.join(preview['columns'])}; first rows: {rows}"
    return f"Question: {question}\nSQL: {sql or '(none)'}\nResult: {result}"


def folds(questions: list[dict], scheme: str) -> dict[int, int]:
    """question_id -> fold. 5-fold: stratified by database. lodo: one fold per database."""
    by_db: dict[str, list[int]] = {}
    for q in questions:
        by_db.setdefault(q["db_id"], []).append(q["question_id"])
    if scheme == "lodo":
        return {qid: k for k, db in enumerate(sorted(by_db)) for qid in by_db[db]}
    rng = random.Random(SEED)
    out: dict[int, int] = {}
    offset = 0
    for db in sorted(by_db):
        ids = sorted(by_db[db])
        rng.shuffle(ids)
        for i, qid in enumerate(ids):
            out[qid] = (i + offset) % FOLDS
        offset += len(ids)
    return out


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


def train_and_score(model_name: str, train: list[tuple[str, int]], test_texts: list[str], device: str) -> list[float]:
    torch.manual_seed(SEED)
    rng = random.Random(SEED)
    hp = HP[model_name]
    tok, model = build(model_name, device)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=hp["lr"], weight_decay=0.01)
    steps = hp["epochs"] * math.ceil(len(train) / hp["batch"])
    warm = max(1, steps // 10)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / warm) * max(0.0, (steps - s) / max(1, steps - warm)))
    model.train()
    step = 0
    for _ in range(hp["epochs"]):
        for batch in batches(train, hp["batch"], True, rng):
            enc = tok([t for t, _ in batch], truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt").to(device)
            labels = torch.tensor([y for _, y in batch], device=device)
            loss = model(**enc, labels=labels).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            sched.step()
            opt.zero_grad()
            step += 1
            if device == "mps" and step % 50 == 0:
                torch.mps.empty_cache()
    model.eval()
    probs: list[float] = []
    with torch.no_grad():
        for i in range(0, len(test_texts), 32):
            enc = tok(test_texts[i:i + 32], truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt").to(device)
            probs += torch.softmax(model(**enc).logits.float(), dim=-1)[:, 1].tolist()
    del model, opt
    if device == "mps":
        torch.mps.empty_cache()
    return probs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", choices=sorted(HF), required=True)
    ap.add_argument("--regime", choices=["real", "mutation"], required=True)
    ap.add_argument("--scheme", choices=["5fold", "lodo"], default="5fold")
    args = ap.parse_args()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    pool = json.loads((EVAL / "paperb_pool.json").read_text())["questions"]
    questions = {q["question_id"]: q for q in pool}
    fold_of = folds(pool, args.scheme)
    out_path = EVAL / f"paperb_scores_{args.model}_{args.regime}_{args.scheme}.json"
    record = json.loads(out_path.read_text()) if out_path.exists() else {
        "model": HF[args.model], "regime": args.regime, "scheme": args.scheme, "hp": HP[args.model],
        "seed": SEED, "folds": {}, "scores": {}}
    for k in sorted(set(fold_of.values())):
        if str(k) in record["folds"]:
            continue
        test_ids = {qid for qid, f in fold_of.items() if f == k}
        train = training_examples(args.regime, questions, set(fold_of) - test_ids)
        keys, texts, index = [], [], {}
        for qid in sorted(test_ids):
            q = questions[qid]
            for i, c in enumerate(q["candidates"]):
                text = render(q["question"], c["sql"], c["preview"])
                if text not in index:          # each distinct candidate is scored once
                    index[text] = len(texts)
                    texts.append(text)
                keys.append((f"{qid}|{i}", text))
        t0 = time.monotonic()
        probs = train_and_score(args.model, train, texts, device)
        for key, text in keys:
            record["scores"][key] = round(probs[index[text]], 5)
        record["folds"][str(k)] = {"test_questions": len(test_ids), "train_examples": len(train),
                                   "train_positive": sum(y for _, y in train), "minutes": round((time.monotonic() - t0) / 60, 1)}
        out_path.write_text(json.dumps(record, indent=1) + "\n")
        print(f"fold {k}: {record['folds'][str(k)]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
