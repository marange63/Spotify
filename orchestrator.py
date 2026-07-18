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
    python orchestrator.py validate review   runs/D/<id>/review.json
    python orchestrator.py approve <id> --date D      # the ONLY path that writes briefings/
    python orchestrator.py mark <id> --date D --status skipped|failed --stage X --reason "…"
    python orchestrator.py status --date D [--json]

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

# Artifact filenames inside runs/<date>/<prompt_id>/
RESEARCH_FILE = "research.json"
PLAN_FILE = "editorial_plan.json"
DRAFT_FILE = "draft.txt"
REVIEW_FILE = "review.json"
FINAL_FILE = "final.txt"


def ordered_enabled(data: dict) -> list:
    """Enabled prompts in pipeline order: normal prompts first, then ``kind=="synthesis"``
    prompts (e.g. The Throughline), which synthesize the others and so must be authored/
    published last — publishing last also gives them the newest ``published_at`` so they
    sort to the top of the feed. Stable within each group."""
    enabled = [p for p in data["prompts"] if p.get("enabled")]
    return sorted(enabled, key=lambda p: p.get("kind") == "synthesis")


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

    plan = {"date": date, "novelty": novelty, "prompts": [
        {
            "id": e["id"],
            "name": e["name"],
            "kind": e["kind"],
            "status": e["status"],
            "dir": prompt_dir(date, e["id"]),
            "artifacts": {
                "research": os.path.join(prompt_dir(date, e["id"]), RESEARCH_FILE),
                "plan": os.path.join(prompt_dir(date, e["id"]), PLAN_FILE),
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


_VALIDATORS = {"research": validate_research, "plan": validate_plan, "review": validate_review}


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
