"""Deterministic gates for the four-stage briefing pipeline.

The briefing scripts themselves are produced by four Claude Code subagents
(``.claude/agents/researcher.md`` -> ``analyst-editor.md`` -> ``writer.md`` ->
``reviewer.md`` — the reviewer is an independent fresh-context editor, separate
from the writer) run by the main Claude session, with persistent file handoffs under
``runs/<date>/<prompt_id>/``. This module is NOT an agent runner — it is the
stdlib-only gatekeeper the session calls between stages:

    python orchestrator.py init --date D --novelty strict|relaxed
    python orchestrator.py validate research runs/D/<id>/research.json
    python orchestrator.py validate plan     runs/D/<id>/editorial_plan.json
    python orchestrator.py validate deep     runs/D/<id>/deep_research.json   # optional stage 2.5
    python orchestrator.py validate review   runs/D/<id>/review.json
    python orchestrator.py approve <id> --date D      # the ONLY path that writes briefings/
    python orchestrator.py mark <id> --date D --status skipped|failed --stage X --reason "…"
    python orchestrator.py status --date D [--json]
    python orchestrator.py resume --date D [--prune]   # where to restart an interrupted run

``approve`` copies ``final.txt`` to ``briefings/<id>.txt`` only when
``review.json`` says ``decision: "approve"`` — so an unreviewed or rejected
script can never reach TTS/publishing, regardless of what the session does.
Batch state lives in ``runs/<date>/run.json``; ``status`` reports per-prompt
outcomes and the approved prompt ids for the publishing phase.
"""
import argparse
import datetime
import json
import logging
import os
import shutil
import sys

import config
import library

log = logging.getLogger("orchestrator")

NOVELTY_MODES = ("strict", "relaxed")
MARK_STATUSES = ("skipped", "failed")
STAGES = ("research", "plan", "write", "review")

# Stages a resumed prompt can restart at, in dependency order. "finalize" means every artifact is
# present and consistent — only `approve` (or `mark`, per review.json's decision) is left to run;
# "done" means the prompt is already approved/skipped and must not be touched.
RESUME_STAGES = ("research", "plan", "deep", "write", "review", "finalize", "done")

# Artifacts whose mtime may legitimately equal their upstream's (same-second writes).
_MTIME_SLACK = 1.0

# Artifact filenames inside runs/<date>/<prompt_id>/
# The standing prompt text, written at init so the Researcher (and any stage) reads it from disk
# instead of the parent session embedding ~1.3k tokens of prompt in every dispatch — that embed
# then rides along in the parent's accumulating context for the rest of the run (see run_report's
# per-stage "orchestration" cost). Cheap to write, big to not re-carry.
PROMPT_FILE = "prompt.txt"
RESEARCH_FILE = "research.json"
PLAN_FILE = "editorial_plan.json"
# Optional stage 2.5 — written only when the editorial plan requests a deep dive. Same schema as
# research.json (so the same validator enforces the verbatim-quote contract on its facts).
DEEP_FILE = "deep_research.json"
DRAFT_FILE = "draft.txt"
REVIEW_FILE = "review.json"
FINAL_FILE = "final.txt"


SYNTHESIS_KINDS = ("synthesis", "forecast")  # families authored/published after normal prompts


def ordered_enabled(data: dict) -> list:
    """Enabled prompts in pipeline order: normal prompts first, then the synthesis family
    (``kind`` in ``SYNTHESIS_KINDS`` — The Throughline and The Forward Curve), which build on
    the others and so must be authored/published last — publishing last also gives them the
    newest ``published_at`` so they sort to the top of the feed. Stable within each group."""
    enabled = [p for p in data["prompts"] if p.get("enabled")]
    return sorted(enabled, key=lambda p: p.get("kind") in SYNTHESIS_KINDS)


# --- run state (runs/<date>/run.json) ----------------------------------------

def run_dir(date: str) -> str:
    return os.path.join(config.RUNS_DIR, date)


def prompt_dir(date: str, prompt_id: str) -> str:
    return os.path.join(run_dir(date), prompt_id)


def _state_path(date: str) -> str:
    return os.path.join(run_dir(date), "run.json")


def load_state(date: str) -> dict:
    path = _state_path(date)
    if not os.path.exists(path):
        raise FileNotFoundError(f"no run state at {path} — run `orchestrator.py init` first")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_state(date: str, state: dict) -> None:
    os.makedirs(run_dir(date), exist_ok=True)
    with open(_state_path(date), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _find_entry(state: dict, prompt_id: str) -> dict:
    for e in state["prompts"]:
        if e["id"] == prompt_id:
            return e
    raise KeyError(f"prompt {prompt_id!r} is not in this run — was init run after it was enabled?")


# --- init ---------------------------------------------------------------------

def init_run(date: str, novelty: str) -> dict:
    """Create runs/<date>/<id>/ for every enabled prompt and (re)write run.json.

    Idempotent: re-running the same day preserves the recorded status of prompts
    already in the run (so a resumed batch doesn't lose approvals); newly-enabled
    prompts are added as pending. Returns the plan for the session to follow.
    """
    if novelty not in NOVELTY_MODES:
        raise ValueError(f"novelty must be one of {NOVELTY_MODES}")
    data = library.load()
    prompts = ordered_enabled(data)

    prior = {}
    if os.path.exists(_state_path(date)):
        prior = {e["id"]: e for e in load_state(date)["prompts"]}

    entries = []
    for p in prompts:
        pdir = prompt_dir(date, p["id"])
        os.makedirs(pdir, exist_ok=True)
        # Persist the standing prompt so agents read it from disk (into their own fresh context)
        # rather than the parent embedding it in every dispatch. Rewritten each init so an edited
        # prompt is always current.
        with open(os.path.join(pdir, PROMPT_FILE), "w", encoding="utf-8") as f:
            f.write((p.get("prompt") or "").strip() + "\n")
        old = prior.get(p["id"], {})
        entries.append({
            "id": p["id"],
            "name": p["name"],
            "kind": p.get("kind") or "normal",
            "status": old.get("status", "pending"),
            "stage": old.get("stage"),
            "reason": old.get("reason"),
        })

    state = {"date": date, "novelty": novelty, "prompts": entries}
    _save_state(date, state)

    # The plan carries each prompt's resume point, so a retry restarts at the first missing or
    # superseded artifact instead of re-running finished stages (see resume_for_prompt). On a
    # first run every prompt is empty and this simply reports "research" for all of them.
    plan = {"date": date, "novelty": novelty, "prompts": [
        {
            "id": e["id"],
            "name": e["name"],
            "kind": e["kind"],
            "status": e["status"],
            "dir": prompt_dir(date, e["id"]),
            "resume": resume_for_prompt(date, e),
            "artifacts": {
                "prompt": os.path.join(prompt_dir(date, e["id"]), PROMPT_FILE),
                "research": os.path.join(prompt_dir(date, e["id"]), RESEARCH_FILE),
                "plan": os.path.join(prompt_dir(date, e["id"]), PLAN_FILE),
                "deep": os.path.join(prompt_dir(date, e["id"]), DEEP_FILE),
                "draft": os.path.join(prompt_dir(date, e["id"]), DRAFT_FILE),
                "review": os.path.join(prompt_dir(date, e["id"]), REVIEW_FILE),
                "final": os.path.join(prompt_dir(date, e["id"]), FINAL_FILE),
            },
        } for e in entries
    ]}
    return plan


# --- artifact validation --------------------------------------------------------

def _need(doc: dict, key: str, typ, errors: list) -> bool:
    """Require doc[key] of type ``typ``; record a readable error. True if present+typed."""
    if key not in doc:
        errors.append(f"missing required key: {key}")
        return False
    if typ is not None and not isinstance(doc[key], typ):
        errors.append(f"{key} must be {typ.__name__}, got {type(doc[key]).__name__}")
        return False
    return True


def _need_enum(doc: dict, key: str, allowed, errors: list) -> None:
    if _need(doc, key, str, errors) and doc[key] not in allowed:
        errors.append(f"{key} must be one of {sorted(allowed)}, got {doc[key]!r}")


def validate_research(doc: dict) -> list:
    """Structural check of research.json. Returns a list of problems (empty = valid)."""
    errors = []
    _need(doc, "prompt_id", str, errors)
    _need(doc, "run_date", str, errors)
    _need_enum(doc, "status", ("complete", "insufficient", "failed"), errors)
    for key in ("lead_candidates", "secondary_items", "items_to_ignore", "research_gaps"):
        _need(doc, key, list, errors)
    if doc.get("status") == "complete":
        leads = doc.get("lead_candidates") or []
        if not leads:
            errors.append('status "complete" requires at least one lead candidate')
        for i, c in enumerate(leads):
            if not isinstance(c, dict):
                errors.append(f"lead_candidates[{i}] must be an object")
                continue
            for key, typ in (("title", str), ("summary", str), ("sources", list)):
                if key not in c or not isinstance(c[key], typ):
                    errors.append(f"lead_candidates[{i}].{key} missing or not {typ.__name__}")
            if isinstance(c.get("title"), str) and not c["title"].strip():
                errors.append(f"lead_candidates[{i}].title is empty")
            # Figure-verification contract: every important fact must carry a verbatim
            # supporting quote (the reviewer audits the script's numbers against these).
            for j, fact in enumerate(c.get("important_facts") or []):
                where = f"lead_candidates[{i}].important_facts[{j}]"
                if not isinstance(fact, dict):
                    errors.append(f"{where} must be an object with fact/quote/source_url "
                                  "(verbatim-quote contract)")
                    continue
                for key in ("fact", "quote"):
                    if not isinstance(fact.get(key), str) or not fact[key].strip():
                        errors.append(f"{where}.{key} missing or empty")
    return errors


# Bounds on the optional stage-2.5 deep dive. Enforced here, not just in the agent prompt: the
# deep researcher's cost is superlinear in its tool calls, so the request itself is what must be
# capped to keep the batch's token budget predictable.
MAX_DEEP_DIVE_REQUESTS = 1
MAX_DEEP_DIVE_QUESTIONS = 3


def _validate_deep_dive_requests(doc: dict, errors: list) -> None:
    """Check editorial_plan.json's optional ``deep_dive_requests``. ``[]`` is the normal case."""
    if not _need(doc, "deep_dive_requests", list, errors):
        return
    reqs = doc["deep_dive_requests"]
    if len(reqs) > MAX_DEEP_DIVE_REQUESTS:
        errors.append(f"deep_dive_requests holds {len(reqs)} entries, "
                      f"max is {MAX_DEEP_DIVE_REQUESTS}")
    if reqs and doc.get("decision") == "skip":
        errors.append('decision "skip" must not request a deep dive (there is no script to support)')
    approved = {item.get("research_item") for item in (doc.get("approved_items") or [])
                if isinstance(item, dict)}
    for i, req in enumerate(reqs):
        where = f"deep_dive_requests[{i}]"
        if not isinstance(req, dict):
            errors.append(f"{where} must be an object with research_item/questions")
            continue
        item = req.get("research_item")
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{where}.research_item missing or empty")
        elif doc.get("decision") == "write" and item not in approved:
            errors.append(f"{where}.research_item {item!r} does not match any approved item")
        questions = req.get("questions")
        if not isinstance(questions, list):
            errors.append(f"{where}.questions missing or not a list")
            continue
        if not questions:
            errors.append(f"{where}.questions is empty — omit the request instead")
        if len(questions) > MAX_DEEP_DIVE_QUESTIONS:
            errors.append(f"{where}.questions holds {len(questions)} entries, "
                          f"max is {MAX_DEEP_DIVE_QUESTIONS}")
        for j, q in enumerate(questions):
            if not isinstance(q, str) or not q.strip():
                errors.append(f"{where}.questions[{j}] must be a non-empty string")


def validate_plan(doc: dict) -> list:
    """Structural check of editorial_plan.json. ``decision: "skip"`` is a valid outcome."""
    errors = []
    _need(doc, "prompt_id", str, errors)
    _need(doc, "run_date", str, errors)
    _need_enum(doc, "decision", ("write", "skip"), errors)
    _need(doc, "decision_reason", str, errors)
    if doc.get("decision") == "write":
        for key in ("central_thesis", "lead_story"):
            if _need(doc, key, str, errors) and not doc[key].strip():
                errors.append(f"{key} is empty")
        if _need(doc, "approved_items", list, errors):
            if not doc["approved_items"]:
                errors.append('decision "write" requires at least one approved item')
            for i, item in enumerate(doc["approved_items"]):
                if not isinstance(item, dict):
                    errors.append(f"approved_items[{i}] must be an object")
                    continue
                if not isinstance(item.get("research_item"), str) or not item["research_item"].strip():
                    errors.append(f"approved_items[{i}].research_item missing or empty")
                if item.get("treatment") not in ("lead", "major", "brief"):
                    errors.append(f"approved_items[{i}].treatment must be lead|major|brief")
        _need(doc, "recommended_structure", list, errors)
    _validate_deep_dive_requests(doc, errors)
    return errors


def validate_review(doc: dict) -> list:
    """Structural check of review.json."""
    errors = []
    _need(doc, "prompt_id", str, errors)
    _need(doc, "run_date", str, errors)
    _need_enum(doc, "decision", ("approve", "skip", "failed"), errors)
    _need(doc, "decision_reason", str, errors)
    for key in ("issues_found", "changes_made"):
        _need(doc, key, list, errors)
    if _need(doc, "scores", dict, errors):
        for key in ("novelty", "factual_support", "analytical_depth", "editorial_quality",
                    "audio_flow", "prompt_compliance", "overall"):
            v = doc["scores"].get(key)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errors.append(f"scores.{key} missing or not a number")
    return errors


# "deep" deliberately reuses the research validator: deep_research.json shares research.json's
# schema, so the verbatim-quote contract is enforced on deep-dive facts by the same code path.
_VALIDATORS = {"research": validate_research, "plan": validate_plan, "review": validate_review,
               "deep": validate_research}


def validate_file(kind: str, path: str) -> list:
    """Validate the artifact at ``path`` as ``kind``. Returns a list of problems."""
    if kind not in _VALIDATORS:
        raise ValueError(f"kind must be one of {sorted(_VALIDATORS)}")
    if not os.path.exists(path):
        return [f"file not found: {path}"]
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e}"]
    if not isinstance(doc, dict):
        return ["top level must be a JSON object"]
    return _VALIDATORS[kind](doc)


# --- resume (artifact-aware) ----------------------------------------------------
#
# Why this exists: run.json tracks status per PROMPT, which is enough to skip finished prompts but
# not to restart an unfinished one at the right STAGE. Before this, a retry re-ran the whole
# pipeline from the Researcher even when research/plan/draft were already on disk — burning the
# budget that made it a retry in the first place (observed 2026-07-25: the Opus retry rewrote
# research.json and editorial_plan.json on top of complete artifacts, then died).
#
# The second, nastier half of that bug: when an upstream artifact IS rewritten, the downstream ones
# built from the OLD version survive on disk and nothing flags the mismatch. On 2026-07-25 the
# surviving deep_research.json/draft.txt answered a superseded plan (dives about Bitcoin whale flows
# and the Ratepayer Pledge; plans about the CLARITY Act and oil chokepoints). A naive resume would
# have handed the Writer a plan and a dive covering different stories, and the output would have
# looked plausible. So staleness is detected by mtime against the upstream artifact, and everything
# downstream of the resume point is reported as stale (and deleted with --prune).


def _nonempty(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return bool(f.read().strip())
    except OSError:
        return False


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _stage_chain(date: str, prompt_id: str, kind: str) -> list:
    """[(stage, [artifact paths], validator kind or None)] in dependency order.

    Synthesis-family prompts (the Throughline, the Forward Curve) are not researched and have no
    editorial plan — they run their writer-role agent then the Reviewer over the day's approved
    briefings (and, for the forecast, the recent transcript archive) — so their chain starts at
    "write".
    """
    d = prompt_dir(date, prompt_id)
    j = lambda n: os.path.join(d, n)  # noqa: E731
    review = ("review", [j(REVIEW_FILE), j(FINAL_FILE)], "review")
    if kind in SYNTHESIS_KINDS:
        return [("write", [j(DRAFT_FILE)], None), review]
    return [
        ("research", [j(RESEARCH_FILE)], "research"),
        ("plan", [j(PLAN_FILE)], "plan"),
        ("deep", [j(DEEP_FILE)], "deep"),
        ("write", [j(DRAFT_FILE)], None),
        review,
    ]


def resume_for_prompt(date: str, entry: dict) -> dict:
    """Decide which stage ``entry``'s prompt should restart at, and which artifacts are stale.

    Walks the stage chain in dependency order and stops at the first artifact that is missing,
    empty, schema-invalid, or OLDER than the upstream artifact it was derived from. Everything from
    that stage onward is stale by construction and reported for deletion.
    """
    pid = entry["id"]
    kind = entry.get("kind") or "normal"
    out = {"id": pid, "name": entry.get("name", pid), "kind": kind,
           "status": entry["status"], "resume_stage": None, "reason": "", "stale_artifacts": []}

    if entry["status"] in ("approved", "skipped"):
        out["resume_stage"] = "done"
        out["reason"] = f"status is {entry['status']} — do not re-run"
        return out

    chain = _stage_chain(date, pid, kind)
    d = prompt_dir(date, pid)
    upstream = 0.0
    resume = reason = None
    cut = len(chain)

    for idx, (stage, paths, vkind) in enumerate(chain):
        if stage == "deep":
            # Stage 2.5 is conditional: it runs only when the CURRENT plan asks for it.
            plan_doc = _read_json(os.path.join(d, PLAN_FILE)) or {}
            wants = bool(plan_doc.get("deep_dive_requests"))
            exists = os.path.exists(paths[0])
            if not wants:
                if exists:
                    # A dive answering a plan that no longer asks for one — the exact mismatch
                    # that shipped undetected on 2026-07-25.
                    resume, cut = "write", idx
                    reason = ("deep_research.json exists but the current plan requests no deep "
                              "dive — superseded by a re-planned prompt")
                    break
                continue  # correctly absent; nothing to validate, no mtime to inherit

        missing = [p for p in paths if not _nonempty(p)]
        if missing:
            resume, cut = stage, idx
            reason = f"missing or empty {os.path.basename(missing[0])}"
            break

        mtimes = [os.path.getmtime(p) for p in paths]
        if min(mtimes) < upstream - _MTIME_SLACK:
            resume, cut = stage, idx
            reason = (f"{os.path.basename(paths[0])} is older than the artifact it derives from "
                      "— superseded, must be rebuilt")
            break

        if vkind:
            problems = validate_file(vkind, paths[0])
            if problems:
                resume, cut = stage, idx
                reason = f"{os.path.basename(paths[0])} invalid: {problems[0]}"
                break

        # A plan that says "skip" ends the prompt — there is nothing downstream to build.
        if stage == "plan":
            plan_doc = _read_json(paths[0]) or {}
            if plan_doc.get("decision") == "skip":
                resume, cut = "finalize", len(chain)
                reason = ('editorial plan decision is "skip" — mark the prompt skipped, '
                          "do not write it")
                break

        upstream = max(upstream, max(mtimes))
    else:
        resume, cut = "finalize", len(chain)
        reason = ("all artifacts present and consistent — run approve "
                  "(or mark, per review.json's decision)")

    out["resume_stage"] = resume
    out["reason"] = reason
    # Everything from the resume point on was built on something being rebuilt (or is invalid).
    stale = []
    for stage, paths, _ in chain[cut:]:
        stale.extend(p for p in paths if os.path.exists(p))
    out["stale_artifacts"] = stale
    return out


def resume_plan(date: str) -> dict:
    """Per-prompt resume points for the whole batch (see ``resume_for_prompt``)."""
    state = load_state(date)
    prompts = [resume_for_prompt(date, e) for e in state["prompts"]]
    return {"date": state["date"], "novelty": state["novelty"], "prompts": prompts,
            "unfinished": [p["id"] for p in prompts if p["resume_stage"] != "done"]}


def prune_stale(plan: dict) -> list:
    """Delete every artifact the resume plan flagged as stale. Returns the paths removed.

    These are regenerable local artifacts under the gitignored ``runs/<date>/``; deleting them is
    what makes a resume safe, since a superseded artifact left in place will be silently consumed
    by the next stage.
    """
    removed = []
    for p in plan["prompts"]:
        for path in p["stale_artifacts"]:
            try:
                os.remove(path)
            except OSError as e:
                log.warning("could not remove stale artifact %s (%s)", path, e)
                continue
            removed.append(path)
            log.info("pruned stale artifact %s (%s: resume at %s)",
                     os.path.relpath(path, config.RUNS_DIR), p["id"], p["resume_stage"])
    return removed


# --- approve / mark / status ----------------------------------------------------

def approve(prompt_id: str, date: str) -> str:
    """Copy runs/<date>/<id>/final.txt to briefings/<id>.txt — ONLY if review.json
    validates and says ``decision: "approve"`` and final.txt is non-empty. This is
    the single gate between the pipeline and TTS/publishing. Returns the briefing path.
    """
    pdir = prompt_dir(date, prompt_id)
    review_path = os.path.join(pdir, REVIEW_FILE)
    final_path = os.path.join(pdir, FINAL_FILE)

    problems = validate_file("review", review_path)
    if problems:
        raise RuntimeError(f"approve refused — review.json invalid: {'; '.join(problems)}")
    with open(review_path, encoding="utf-8") as f:
        review = json.load(f)
    if review["decision"] != "approve":
        raise RuntimeError(f'approve refused — review decision is {review["decision"]!r}, '
                           f'not "approve" ({review.get("decision_reason", "")})')
    if not os.path.exists(final_path):
        raise RuntimeError(f"approve refused — missing or empty {final_path}")
    with open(final_path, encoding="utf-8") as f:
        if not f.read().strip():
            raise RuntimeError(f"approve refused — missing or empty {final_path}")

    os.makedirs(config.BRIEFINGS_DIR, exist_ok=True)
    dest = os.path.join(config.BRIEFINGS_DIR, prompt_id + ".txt")
    shutil.copyfile(final_path, dest)

    state = load_state(date)
    entry = _find_entry(state, prompt_id)
    entry.update(status="approved", stage=None, reason=None)
    _save_state(date, state)
    log.info("approved %s -> %s", prompt_id, dest)
    return dest


def mark(prompt_id: str, date: str, status: str, stage: str | None, reason: str) -> None:
    """Record a non-approval outcome (skipped/failed at some stage) in run.json."""
    if status not in MARK_STATUSES:
        raise ValueError(f"status must be one of {MARK_STATUSES}")
    if stage is not None and stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    state = load_state(date)
    entry = _find_entry(state, prompt_id)
    entry.update(status=status, stage=stage, reason=reason)
    _save_state(date, state)
    log.info("marked %s %s (stage=%s): %s", prompt_id, status, stage, reason)


def run_status(date: str) -> dict:
    """Batch outcome summary: per-prompt statuses plus the approved prompt ids."""
    state = load_state(date)
    approved = [e["id"] for e in state["prompts"] if e["status"] == "approved"]
    return {"date": state["date"], "novelty": state["novelty"],
            "prompts": state["prompts"], "approved": approved}


# --- CLI ------------------------------------------------------------------------

def main(argv=None) -> int:
    config.configure_logging()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    today = datetime.date.today().isoformat()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create run dirs + run.json for every enabled prompt")
    p.add_argument("--date", default=today)
    p.add_argument("--novelty", choices=NOVELTY_MODES, default="strict")

    p = sub.add_parser("validate", help="schema-check a pipeline artifact")
    p.add_argument("kind", choices=sorted(_VALIDATORS))
    p.add_argument("path")

    p = sub.add_parser("approve", help="copy final.txt to briefings/ if the review approves")
    p.add_argument("prompt_id")
    p.add_argument("--date", default=today)

    p = sub.add_parser("mark", help="record a skipped/failed outcome for a prompt")
    p.add_argument("prompt_id")
    p.add_argument("--date", default=today)
    p.add_argument("--status", choices=MARK_STATUSES, required=True)
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--reason", default="")

    p = sub.add_parser("status", help="per-prompt outcomes + approved ids")
    p.add_argument("--date", default=today)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("resume", help="per-prompt resume stage + superseded artifacts")
    p.add_argument("--date", default=today)
    p.add_argument("--json", action="store_true")
    p.add_argument("--prune", action="store_true",
                   help="delete the superseded artifacts (required before resuming, so a stale "
                        "artifact can't be silently consumed by the next stage)")

    args = ap.parse_args(argv)

    if args.cmd == "init":
        plan = init_run(args.date, args.novelty)
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "validate":
        problems = validate_file(args.kind, args.path)
        if problems:
            print(f"INVALID {args.kind}: {args.path}")
            for msg in problems:
                print(f"  - {msg}")
            return 1
        print(f"OK {args.kind}: {args.path}")
        return 0

    if args.cmd == "approve":
        try:
            dest = approve(args.prompt_id, args.date)
        except (RuntimeError, FileNotFoundError, KeyError) as e:
            print(str(e))
            return 1
        print(f"approved: {dest}")
        return 0

    if args.cmd == "mark":
        try:
            mark(args.prompt_id, args.date, args.status, args.stage, args.reason)
        except (FileNotFoundError, KeyError) as e:
            print(str(e))
            return 1
        return 0

    if args.cmd == "resume":
        try:
            plan = resume_plan(args.date)
        except FileNotFoundError as e:
            print(str(e))
            return 1
        removed = prune_stale(plan) if args.prune else []
        if args.prune:
            # Re-derive after deleting so the reported stale list reflects the cleaned tree.
            plan = resume_plan(args.date)
            plan["pruned"] = removed
        if args.json:
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        else:
            print(f"resume plan {plan['date']} — novelty: {plan['novelty']}")
            for e in plan["prompts"]:
                if e["resume_stage"] == "done":
                    print(f"  {e['id']}: done ({e['status']})")
                    continue
                print(f"  {e['id']}: resume at {e['resume_stage']} — {e['reason']}")
                for path in e["stale_artifacts"]:
                    print(f"      stale: {os.path.basename(path)}")
            if removed:
                print(f"pruned {len(removed)} superseded artifact(s)")
            print(f"unfinished: {', '.join(plan['unfinished']) or '(none)'}")
        return 0

    if args.cmd == "status":
        try:
            summary = run_status(args.date)
        except FileNotFoundError as e:
            print(str(e))
            return 1
        if args.json:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            print(f"run {summary['date']} — novelty: {summary['novelty']}")
            for e in summary["prompts"]:
                extra = f" [{e['stage']}] {e['reason']}" if e["status"] in MARK_STATUSES else ""
                print(f"  {e['id']}: {e['status']}{extra}")
            print(f"approved: {', '.join(summary['approved']) or '(none)'}")
        return 0

    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
