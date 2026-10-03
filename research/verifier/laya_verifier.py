"""
research/verifier/laya_verifier.py
Laya as a SQL verifier (README.md): zero-shot scoring, setup-time fine-tuning, and scoring of a
fine-tuned checkpoint. The training loop is Convai's recipe as ported for one Apple-silicon GPU
in github.com/mani-aiml/jev-demos (laya-finetune/train.py, MIT): a policy gradient on a
proper-scoring reward plus soft cross-entropy, effective batch 64, held-out temperature fit.
`--loss ce` drops the policy-gradient term: the ablation.

    python laya_verifier.py score --model base --data out/test_llm_gpt-oss-120b.jsonl --out out/score_base_llm.json
    python laya_verifier.py train --loss rlcd --out runs/rlcd
    python laya_verifier.py score --model runs/rlcd --data out/test_mut.jsonl --out out/score_rlcd_mut.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from safetensors.torch import load_file, save_file
from transformers import AutoTokenizer
from laya.agent import _fix_tokenizer_config
from laya.common import QTYPES, build_model, build_sequence, collate_items, proper_reward, render_options

HERE = Path(__file__).resolve().parent
MODEL_ID, SUBFOLDER, REVISION = "convaiinnovations/laya", "typed-decisions", "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
MICRO_BATCH, GRAD_ACCUM, GROUP_SIZE = 4, 16, 4  # effective batch 64 as in the recipe; micro-batch 4 fits beside Docker in 24 GB
LR_ENCODER, LR_HEAD = 2.5e-5, 1.0e-4
SIGMA_START, SIGMA_END = 0.4, 0.1
CALIB_SHARE, SEED = 0.1, 20261002
DEVICE = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

OPTIONS = {
    "correct": "the SQL computes exactly what the question asks, and the result answers it",
    "wrong": "the SQL computes something else: wrong filter, aggregate, join, grouping, order or limit",
}
QUESTION = {"type": "choice", "instructions": "Does the SQL correctly answer the QUESTION?", "criteria": OPTIONS}
KEYS = list(OPTIONS)


def base_checkpoint() -> str:
    root = snapshot_download(MODEL_ID, revision=REVISION, allow_patterns=[f"{SUBFOLDER}/*"])
    path = os.path.join(root, SUBFOLDER)
    _fix_tokenizer_config(path)
    return path


def load_model(model_dir: str):
    cfg = json.load(open(os.path.join(model_dir, "rl_agent_config.json")))
    model = build_model(cfg, encoder_dir=os.path.join(model_dir, "encoder"))
    model.load_state_dict(load_file(os.path.join(model_dir, "model.safetensors")), strict=True)
    tok_dir = os.path.join(model_dir, "tokenizer")
    return model.to(DEVICE), cfg, AutoTokenizer.from_pretrained(tok_dir)


def state_of(row: dict) -> dict:
    return {"QUESTION": row["question"], "SQL": row["sql"], "RESULT": row["result"]}


def to_items(rows: list[dict], tok, cfg) -> tuple[list[dict], int]:
    """One sequence per row; (items, rows dropped because an option marker was cut off)."""
    internal = {"t": QUESTION["type"], "ins": QUESTION["instructions"], "crit": QUESTION["criteria"]}
    items, dropped = [], 0
    for row in rows:
        ids, markers = build_sequence(tok, state_of(row), internal, cfg["max_len"], cfg["head_max_len"])
        if len(markers) != len(render_options(internal)):
            dropped += 1
            continue
        label = 0 if row["correct"] else 1
        items.append({"ids": ids, "markers": markers, "qtype": QTYPES["choice"],
                      "target": [1.0 if i == label else 0.0 for i in range(len(KEYS))], "label": label,
                      "weight": row.get("weight", 1.0), "row": row})
    return items, dropped


def forward(model, batch):
    keys = ("input_ids", "attention_mask", "marker_pos", "marker_mask", "qtype")
    with torch.autocast(DEVICE.type, dtype=torch.bfloat16, enabled=DEVICE.type == "mps"):
        logits, act = model(*(batch[k].to(DEVICE) for k in keys))
    return logits.float(), act


def loss_on(model, batch, sigma: float, use_rl: bool):
    logits, act = forward(model, batch)
    mask, target = batch["marker_mask"].to(DEVICE), batch["target"].to(DEVICE)
    w = torch.tensor([m["weight"] for m in batch["meta"]], device=DEVICE)
    ce = -(target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)).sum(-1)
    loss = (ce * w).sum() / w.sum() + 0.0 * act.sum()
    if use_rl:
        k = mask.sum(-1, keepdim=True).float()
        eps = torch.randn((GROUP_SIZE,) + logits.shape, device=DEVICE) * sigma * mask
        eps = (eps - eps.sum(-1, keepdim=True) / k) * mask
        z = logits.detach().unsqueeze(0) + eps
        q = torch.softmax(z.masked_fill(~mask, -1e4), -1)
        with torch.no_grad():
            r = proper_reward(q, target.unsqueeze(0), batch["qtype"].to(DEVICE), mask, w_sph=0.75, w_rps=1.0)
            adv = (r - r.mean(0, keepdim=True)) / ((r - r.mean(0, keepdim=True)).std() + 1e-6)
        logp = -(((z - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma**2)
        loss = loss + (-((adv * logp).mean(0) * w).sum() / w.sum())
    return loss


def balance(rows: list[dict]) -> None:
    """Weight correct and wrong candidates to equal total weight (mutants are mostly wrong)."""
    n_ok = sum(r["correct"] for r in rows)
    n_bad = len(rows) - n_ok
    for r in rows:
        r["weight"] = (len(rows) / (2 * n_ok)) if r["correct"] else (len(rows) / (2 * n_bad))


def train(args) -> None:
    rows = [json.loads(l) for l in open(HERE / "out" / "train.jsonl")]
    for extra in args.extra or []:  # README addition 3, variant B: the generator's own labelled candidates
        rows += [{**r, "source": r.get("source", "llm")} for r in (json.loads(l) for l in open(HERE / extra)) if r.get("runs", True)]
    # Calibration slice by QUESTION, so no question's candidates are on both sides.
    qids = sorted({r["qid"] for r in rows})
    random.Random(SEED).shuffle(qids)
    calib_q = set(qids[: max(1, int(len(qids) * CALIB_SHARE))])
    train_rows = [r for r in rows if r["qid"] not in calib_q]
    calib_rows = [r for r in rows if r["qid"] in calib_q]
    balance(train_rows)
    torch.manual_seed(SEED)
    model, cfg, tok = load_model(base_checkpoint())
    if hasattr(model.encoder, "gradient_checkpointing_enable"):
        model.encoder.gradient_checkpointing_enable()  # activations recomputed in backward: far less GPU memory
    items, dropped = to_items(train_rows, tok, cfg)
    calib, _ = to_items(calib_rows, tok, cfg)
    print(f"{len(train_rows)} train rows -> {len(items)} sequences ({dropped} dropped: markers cut); "
          f"{len(calib)} calibration; correct share {sum(r['correct'] for r in train_rows) / len(train_rows):.2f}", flush=True)
    enc = [p for n, p in model.named_parameters() if n.startswith("encoder.")]
    head = [p for n, p in model.named_parameters() if not n.startswith("encoder.")]
    opt = torch.optim.AdamW([{"params": enc, "lr": LR_ENCODER}, {"params": head, "lr": LR_HEAD}], weight_decay=0.01)
    updates = max(1, len(items) // (MICRO_BATCH * GRAD_ACCUM) * args.epochs)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=updates, eta_min=1e-6)
    model.train()
    step, t0 = 0, time.time()
    for epoch in range(args.epochs):
        sigma = SIGMA_START + (SIGMA_END - SIGMA_START) * epoch / max(1, args.epochs - 1)
        random.Random(SEED + epoch).shuffle(items)
        for start in range(0, len(items), MICRO_BATCH):
            batch = collate_items([items[start:start + MICRO_BATCH]], tok.pad_token_id)
            loss = loss_on(model, batch, sigma, args.loss == "rlcd") / GRAD_ACCUM
            loss.backward()
            step += 1
            if step % GRAD_ACCUM == 0 or start + MICRO_BATCH >= len(items):
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step(), sched.step(), opt.zero_grad(set_to_none=True)
                if DEVICE.type == "mps":
                    torch.mps.empty_cache()  # variable sequence lengths otherwise grow the MPS cache to ~19 GB (measured)
            if step % 25 == 0:
                print(f"epoch {epoch + 1}/{args.epochs} step {step} loss {loss.item() * GRAD_ACCUM:.4f} "
                      f"{step * MICRO_BATCH / (time.time() - t0):.1f} seq/s", flush=True)
    print(f"trained {step} steps in {time.time() - t0:.0f} s", flush=True)
    temp = fit_temperature(model, calib, tok.pad_token_id)
    out = HERE / args.out
    os.makedirs(out, exist_ok=True)
    save_file({k: v.half().contiguous().cpu() for k, v in model.state_dict().items()}, str(out / "model.safetensors"))
    model.encoder.config.save_pretrained(out / "encoder")
    tok.save_pretrained(out / "tokenizer")
    json.dump({**cfg, "fine_tuned": True, "model_name": f"sql-verifier-{args.loss}", "temperature": [temp, 1.0, 1.0],
               "train_seconds": round(time.time() - t0)}, open(out / "rl_agent_config.json", "w"), indent=2)
    print(f"saved {out}; choice temperature {temp:.3f}")


@torch.no_grad()
def logits_of(model, items: list[dict], pad_id: int) -> list[torch.Tensor]:
    model.eval()
    out = []
    for start in range(0, len(items), 16):
        chunk = items[start:start + 16]
        logits, _ = forward(model, collate_items([chunk], pad_id))
        out.extend(row[: len(it["markers"])].cpu() for row, it in zip(logits, chunk))
    return out


def fit_temperature(model, calib: list[dict], pad_id: int) -> float:
    if len(calib) < 10:
        return 1.0
    Z = torch.stack(logits_of(model, calib, pad_id))
    T = torch.tensor([it["target"] for it in calib])
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=100)

    def closure():
        opt.zero_grad()
        loss = -(T * torch.log_softmax(Z / log_t.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    opt.step(closure)
    return float(torch.clamp(log_t.exp(), 0.1, 10.0).item())


def score(args) -> None:
    model_dir = base_checkpoint() if args.model == "base" else str(HERE / args.model)
    model, cfg, tok = load_model(model_dir)
    temp = (cfg.get("temperature") or [1.0])[0] if args.model != "base" else 1.0
    rows = [json.loads(l) for l in open(HERE / args.data) if l.strip()]
    rows = [r for r in rows if r.get("runs", True)]
    items, dropped = to_items(rows, tok, cfg)
    t0 = time.perf_counter()
    logits = logits_of(model, items, tok.pad_token_id)
    per_item_ms = (time.perf_counter() - t0) * 1000 / max(1, len(items))
    scored = []
    for it, z in zip(items, logits):
        p = torch.softmax(z / temp, -1)[0].item()  # P(correct)
        r = it["row"]
        scored.append({"qid": r["qid"], "correct": r["correct"], "p_correct": p, "source": r.get("source"),
                       "operator": r.get("operator")})
    json.dump({"model": args.model, "data": args.data, "dropped": dropped, "temperature": temp,
               "ms_per_item_batched": round(per_item_ms, 1), "rows": scored}, open(HERE / args.out, "w"), indent=1)
    print(f"scored {len(scored)} ({dropped} dropped) with {args.model} on {args.data}; {per_item_ms:.1f} ms/item batched")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--loss", choices=("rlcd", "ce"), default="rlcd")
    t.add_argument("--epochs", type=int, default=4)
    t.add_argument("--extra", nargs="*", help="more labelled candidate files to train on (variant B)")
    t.add_argument("--out", required=True)
    s = sub.add_parser("score")
    s.add_argument("--model", required=True)
    s.add_argument("--data", required=True)
    s.add_argument("--out", required=True)
    args = ap.parse_args()
    train(args) if args.cmd == "train" else score(args)


if __name__ == "__main__":
    main()
