# Pre-submission notes (2026-10-05)

Draft: `docs/paper/latex/main.pdf` (4 pages with references; source `main.tex`, `refs.bib`; figure from
`scripts/plot_bird.py`). Long-form draft with every number: `docs/paper/evidence_checks_draft.md`.
Full results: `docs/eval/bird_v2_report.md`. Protocol and change log: `docs/research/evidence_tiers_study.md`.

## Is it worth submitting?

Yes, as a modest short paper (workshop, Findings-style short, or a regional conference). Not a main-track
headline.
- **Pre-registered negative result, with its reason.** Data checks rarely fire and do not beat a judge
  at a matched flag rate. Blind error annotation shows why: most errors are semantic.
- **A positive, practical finding.**
  - Process signals (failed, empty, retried) are free and precise.
  - Self-consistency ranks best.
  - Answers that flip between runs are exactly the ones whose samples disagree (exploratory).
- **Concrete numbers on corrected BIRD.**
  - Corrected labels move accuracy by 10.7 points.
  - A fifth of the remaining errors are formatting only.
  - Running the benchmark on PostgreSQL adds dialect artefacts.

## What reviewers will attack

1. **The main claim is a null result with a wide interval.** The checks catch −1.8 vs the judge (95% CI
   −6.3 to 2.2): this does not rule out a small advantage. Phrase it as "no evidence of an advantage,
   and they fire on 4%", which the draft does; keep it that way.
2. **H1, the fan-out hypothesis, was never testable.** The models made 0 and 5 double counts, against
   10 needed. The set is textbook and AI-drafted. This is reported honestly, but it is the weakest part.
3. **One main SQL model, 393 questions.** The second model appears only on the fan-out set.
4. **No human error annotation.** Both annotators are models (κ = 0.74). A 20-item human pass would
   blunt this (see below).
5. **Novelty is modest.** SQLens (Gong et al., 2025) combines database and LLM signals. Our angle is
   evaluation (matched rate, corrected labels, pre-registration, error annotation), not a new method.
6. **Unequal cost.** Self-consistency costs 5 extra calls and the checks cost none. The paper says so;
   a cost-matched comparison would be stronger.
7. **Hosting.** Proxies and a reseller for some runs. Served model names are logged, and agreement
   checks are reported, but a reviewer may still question reproducibility.

## Before submission (in order)

1. **Venue:** ARR 12 Oct (NAACL 2027) or MoSICom 15 Oct first. Still the owner's call.
2. **Official template.** Fetching the ACL style files (acl-org/acl-style-files on GitHub) needs the
   owner's OK. Move the body into it and recheck the page limit.
3. **Human annotation sample, about 20 minutes.** The owner labels 18–20 wrong answers blind
   (`docs/eval/bird_worksheet_errors_heldout_gpt5mini_owner.json`, checklist
   `docs/eval/bird_owner_annotation.md`). We then report human-vs-model agreement.
4. **References.** Confirm Li et al. 2023 (BIRD) and Wang et al. 2023 against the proceedings. The
   others were checked on arXiv.
5. **Anonymous artefact link** (e.g. an anonymised repository) for the protocol, frozen sets and
   scripts.
6. **ARR Responsible NLP checklist**, and reviewer registration if required.
7. **Push the branch.** Local commits await the owner's OK.

## Product changes made after the study (do not affect any reported number)

Measured runs predate these commits:
- **Figure grounding skips database answers whose query never ran** (`50f699f`). On BIRD these failure
  texts were 3 of grounding's 4 catches.
- **The fan-out probe reads aggregates over one table's expressions** (`8d7c3f5`). This was the robustness
  run's single miss, `AVG(EXTRACT(YEAR FROM d.dob))`.
- **The synthesizer asks once more on an empty answer** (`cd37011`). The reseller host returned empty
  texts on 6 of 93 answers.
- **Unused imports removed in the LLM adapters** (`41461b5`).
- **OpenRouter provider pinning and a Cheaper Inference endpoint** (`9f04a42`, `c49d5e7`). Both are
  optional routes; the shipped provider is still Groq.

## Spend

- **Zed:** under $0.3 tonight (Gemini judge on 93 robustness answers, GPT-5 mini repeat run of 50).
- **Cheaper Inference:** about $0.05–0.10 of $15 (0.75M tokens).
- **Groq:** free tier.
