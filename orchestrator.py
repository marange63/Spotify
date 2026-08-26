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
    python orchestrator.py validate script   runs/D/<id>/draft.txt      # or final.txt
    python orchestrator.py validate final_check runs/D/<id>/final_check.json   # stage 5
    python orchestrator.py revision <id> --date D     # charge a revision; exit 3 = out of budget
    python orchestrator.py approve <id> --date D      # the ONLY path that writes briefings/
    python orchestrator.py mark <id> --date D --status skipped|failed --stage X --reason "…"
    python orchestrator.py status --date D [--json]
    python orchestrator.py resume --date D [--prune]   # where to restart an interrupted run

``approve`` copies ``final.txt`` to ``briefings/<id>.txt`` only when
``review.json`` says ``decision: "approve"`` **and** ``final.txt`` itself passes the deterministic
script gate (``script_check``) — so neither an unreviewed/rejected script nor one carrying a stage
direction, a leaked internal artifact, or a word count under its floor can reach TTS/publishing,
regardless of what the session does. The second half matters because ``final.txt`` is the
*reviewer's own* rewrite: it is the one artifact no agent grades independently.
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
import freshness
import library
import script_check

log = logging.getLogger("orchestrator")

NOVELTY_MODES = ("strict", "relaxed")
MARK_STATUSES = ("skipped", "failed")
STAGES = ("research", "plan", "deep", "write", "review", "final_check")

# Stages a resumed prompt can restart at, in dependency order. "finalize" means every artifact is
# present and consistent — only `approve` (or `mark`, per review.json's decision) is left to run;
# "done" means the prompt is already approved/skipped and must not be touched.
RESUME_STAGES = ("research", "plan", "deep", "write", "review", "final_check",
                 "finalize", "done")

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
# Stage 5 — the fresh final reader's verdict on the SHIPPED script (see .claude/agents/final-reader.md).
FINAL_CHECK_FILE = "final_check.json"

# How many times a prompt may be sent back for revision before it is skipped instead. The counter
# lives in run.json, NOT in final_check.json: `resume --prune` deletes the artifact, and phase-1
# runs in chunked sessions that share nothing but disk, so a counter in the artifact would reset
# and the prompt could bounce between reviewer and final-reader forever.
DEFAULT_REVISION_BUDGET = 1
MAX_FINAL_CHECK_ROUNDS = 2


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

def init_run(date: str, novelty: str, now_iso: str | None = None) -> dict:
    """Create runs/<date>/<id>/ for every enabled prompt and (re)write run.json.

    Idempotent: re-running the same day preserves the recorded status of prompts
    already in the run (so a resumed batch doesn't lose approvals); newly-enabled
    prompts are added as pending. Returns the plan for the session to follow.

    ``now_iso`` records the run's as-of *timestamp* (not just its date), so the freshness
    gate knows a print scheduled for 08:30 has not occurred on an overnight run. Defaults to now;
    overridable for re-inits/tests. Also writes ``runs/<date>/run_context.txt`` — the temporal
    anchor the web-research agents read (they otherwise get a date but no time).
    """
    if novelty not in NOVELTY_MODES:
        raise ValueError(f"novelty must be one of {NOVELTY_MODES}")
    data = library.load()
    prompts = ordered_enabled(data)

    prior = {}
    prior_started = None
    if os.path.exists(_state_path(date)):
        _prior_state = load_state(date)
        prior = {e["id"]: e for e in _prior_state["prompts"]}
        prior_started = _prior_state.get("run_started_at")

    # Preserve the original as-of timestamp across idempotent re-inits (a resume must not reset
    # the embargo clock forward past a release that was pending when the run first started).
    run_started_at = now_iso or prior_started \
        or datetime.datetime.now().astimezone().isoformat(timespec="seconds")

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

    state = {"date": date, "novelty": novelty, "run_started_at": run_started_at,
             "prompts": entries}
    _save_state(date, state)

    # Temporal anchor for the run: the as-of time + any releases not yet occurred. Written once
    # per run (shared across prompts); the research/review agents read it so "this morning's
    # print" can be judged against the actual clock, not just the date.
    try:
        now_dt = datetime.datetime.fromisoformat(run_started_at)
    except ValueError:
        now_dt = datetime.datetime.now().astimezone()
    with open(os.path.join(run_dir(date), "run_context.txt"), "w", encoding="utf-8") as f:
        f.write(freshness.run_context_text(now_dt))

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


def _freshness_errors(doc: dict, run_now) -> list:
    """Deterministic freshness/embargo checks on a research/deep dossier (see freshness.py).

    Catches the two ways the 2026-08-12 phantom-CPI fabrication would have entered the pipeline:
    a source URL dated after the run (an impossible/hallucinated source), and a figure reported
    for a scheduled release that had not occurred at the run's as-of time. ``run_now`` (aware
    datetime or None) enables the release gate; the URL-date gate needs only the dossier's own
    ``run_date``.
    """
    errors = []
    ref_date = None
    if isinstance(doc.get("run_date"), str):
        try:
            ref_date = datetime.date.fromisoformat(doc["run_date"])
        except ValueError:
            ref_date = None

    def check_items(items, group):
        for i, c in enumerate(items or []):
            if not isinstance(c, dict):
                continue
            if ref_date is not None:
                for src in c.get("sources") or []:
                    url = src.get("url") if isinstance(src, dict) else None
                    late = freshness.url_date_tokens_after(url, ref_date)
                    if late:
                        errors.append(f"{group}[{i}].sources: URL dated {late} is after the run "
                                      f"date {ref_date} — future-dated (hallucinated/misdated) "
                                      f"source: {url}")
            for j, fact in enumerate(c.get("important_facts") or []):
                if not isinstance(fact, dict):
                    continue
                url = fact.get("source_url")
                if ref_date is not None:
                    late = freshness.url_date_tokens_after(url, ref_date)
                    if late:
                        errors.append(f"{group}[{i}].important_facts[{j}]: source_url dated "
                                      f"{late} is after the run date {ref_date} — future-dated "
                                      f"(hallucinated/misdated) source: {url}")
                text = f"{fact.get('fact', '')} {fact.get('quote', '')}"
                rel = freshness.fact_reports_pending_release(text, run_now)
                if rel:
                    errors.append(
                        f"{group}[{i}].important_facts[{j}]: reports a figure for "
                        f"'{rel['name']}' (releases {rel['datetime'].isoformat(timespec='minutes')}"
                        f"), which has not occurred as of the run "
                        f"({run_now.isoformat(timespec='minutes')}). A scheduled release cannot be "
                        f"stated as fact before it happens — drop the number and frame it as pending.")

    check_items(doc.get("lead_candidates"), "lead_candidates")
    check_items(doc.get("secondary_items"), "secondary_items")
    return errors


def validate_research(doc: dict, run_now=None) -> list:
    """Structural check of research.json plus deterministic freshness/embargo checks.
    Returns a list of problems (empty = valid). ``run_now`` (aware datetime) enables the
    pre-release gate; when None only the schema + URL-date checks run."""
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
    errors += _freshness_errors(doc, run_now)
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


def validate_final_check(doc: dict) -> list:
    """Structural check of final_check.json — the fresh final reader's verdict.

    Two rules carry the weight. A ``revise`` verdict must name at least one HARD defect, so nobody
    can veto a script without saying what is wrong with it; and a ``pass`` may carry no hard defect
    at all, so the reader cannot wave through something it just called broken. Together they make
    the verdict answerable to its own findings — which is exactly what the reviewer's
    ``decision``/``issues_found`` pair does not do (134 approves against a mean of 7 issues).
    """
    errors = []
    _need(doc, "prompt_id", str, errors)
    _need(doc, "run_date", str, errors)
    _need_enum(doc, "verdict", ("pass", "revise", "skip"), errors)
    for key in ("verdict_reason", "listener_question", "answer_heard"):
        if _need(doc, key, str, errors) and not doc[key].strip():
            errors.append(f"{key} must not be empty")
    _need(doc, "answered", bool, errors)

    if _need(doc, "scores", dict, errors):
        for key in ("clarity", "listenability", "payoff"):
            v = doc["scores"].get(key)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errors.append(f"scores.{key} missing or not a number")
            elif not 0 <= v <= 10:
                errors.append(f"scores.{key} must be within 0-10, got {v}")

    rnd = doc.get("revision_round")
    if not isinstance(rnd, int) or isinstance(rnd, bool):
        errors.append("revision_round missing or not an integer")
    elif not 1 <= rnd <= MAX_FINAL_CHECK_ROUNDS:
        errors.append(f"revision_round must be within 1-{MAX_FINAL_CHECK_ROUNDS}, got {rnd}")

    hard = 0
    if _need(doc, "defects", list, errors):
        for i, d in enumerate(doc["defects"]):
            if not isinstance(d, dict):
                errors.append(f"defects[{i}] must be an object")
                continue
            if d.get("severity") not in ("hard", "soft"):
                errors.append(f'defects[{i}].severity must be "hard" or "soft"')
            elif d["severity"] == "hard":
                hard += 1
            for key in ("kind", "quote", "why", "fix"):
                if not isinstance(d.get(key), str) or not d[key].strip():
                    errors.append(f"defects[{i}].{key} missing or empty")

    verdict = doc.get("verdict")
    if verdict == "revise" and not hard:
        errors.append('verdict "revise" requires at least one defect with severity "hard"')
    if verdict == "pass" and hard:
        errors.append(f'verdict "pass" but {hard} hard defect(s) listed — resolve them or revise')
    return errors


SCRIPT_CHECK_SUFFIX = "_script_check.json"


def script_check_path(script_path: str) -> str:
    """Where :func:`validate_script_file` records its metrics for ``script_path``."""
    stem = os.path.splitext(os.path.basename(script_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(script_path)), stem + SCRIPT_CHECK_SUFFIX)


def validate_script_file(path, stage=None, prompt_path=None, enforce_listenability=None,
                         write=True) -> list:
    """Check a spoken script (``draft.txt`` / ``final.txt``) and return its HARD problems.

    ``stage`` defaults from the basename; ``prompt_path`` defaults to ``prompt.txt`` beside the
    script, which supplies the word floor/ceiling. Advisories are recorded in
    ``<stem>_script_check.json`` but never returned — only hard problems fail a gate.

    This is the first validator over the write stage: ``_stage_chain`` passed ``None`` there, so
    until now neither the draft nor the shipped script was checked by anything but the reviewer's
    prose judgement. See ``script_check`` for what "hard" means and why listenability starts
    advisory.
    """
    if not os.path.exists(path):
        return [f"file not found: {path}"]
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if stage is None:
        stage = "draft" if os.path.basename(path).startswith("draft") else "final"
    if prompt_path is None:
        prompt_path = os.path.join(os.path.dirname(os.path.abspath(path)), PROMPT_FILE)
    floor = ceiling = None
    try:
        with open(prompt_path, encoding="utf-8") as f:
            floor, ceiling = script_check.stated_range(f.read())
    except OSError:
        pass  # no standing prompt on disk — skip the length check rather than inventing a bound

    problems = script_check.check(text, floor=floor, ceiling=ceiling, stage=stage,
                                  enforce_listenability=enforce_listenability)
    if write:
        enforce = (script_check.ENFORCE_LISTENABILITY if enforce_listenability is None
                   else enforce_listenability)
        doc = {"path": path, "stage": stage, "floor": floor, "ceiling": ceiling,
               "metrics": script_check.metrics(text),
               "enforce_listenability": enforce, "problems": problems}
        try:
            with open(script_check_path(path), "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except OSError:  # a read-only resume walk must never fail on a metrics side-file
            pass
    return [f"{p['code']}: {p['detail']}" for p in problems if p["severity"] == "error"]


# "deep" deliberately reuses the research validator: deep_research.json shares research.json's
# schema, so the verbatim-quote contract is enforced on deep-dive facts by the same code path.
# "script" is handled separately in validate_file — it validates TEXT, not a JSON document.
_VALIDATORS = {"research": validate_research, "plan": validate_plan, "review": validate_review,
               "deep": validate_research, "final_check": validate_final_check}
_TEXT_KINDS = ("script",)
VALIDATE_KINDS = tuple(sorted(set(_VALIDATORS) | set(_TEXT_KINDS)))


def _strip_html(raw: str) -> str:
    """Crude HTML -> normalized lowercase text for substring matching."""
    import html as _html
    import re as _re
    raw = _re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    raw = _re.sub(r"(?s)<[^>]+>", " ", raw)
    return _re.sub(r"\s+", " ", _html.unescape(raw)).strip().lower()


def _quote_needle(quote: str) -> str:
    """A normalized, shortened form of a dossier quote for tolerant substring matching:
    lowercase, whitespace-collapsed, first ~12 significant words (so trailing paraphrase,
    ellipses, or truncation on the page don't cause false 'not found's)."""
    import re as _re
    q = _re.sub(r"\s+", " ", (quote or "").strip().strip('"“”…. ')).lower()
    words = q.split(" ")
    return " ".join(words[:12]) if len(words) > 12 else q


SOURCE_CHECK_SUFFIX = "_source_check.json"


def source_check_path(dossier_path: str) -> str:
    """Where :func:`verify_sources` records its verdict for ``dossier_path``.

    Derived from the dossier's own stem because ``research.json`` and ``deep_research.json`` live
    in the SAME directory — a single fixed ``source_check.json`` meant the deep-dive check silently
    clobbered the research one, so only the last dossier verified was ever represented on disk.
    """
    stem = os.path.splitext(os.path.basename(dossier_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(dossier_path)), stem + SOURCE_CHECK_SUFFIX)


def verify_sources(path: str, write: bool = True, timeout: float = 12.0) -> tuple:
    """Fetch each figure-bearing source in a research/deep dossier and confirm the verbatim
    quote is on the page. Best-effort and defensive — a fetch that is blocked, paywalled, or
    JS-rendered yields an *advisory*, never a hard failure, so real facts are not stripped on a
    false negative. Only two outcomes are hard (severity 'error'): a future-dated source URL
    (deterministic, no network) and a source that definitively does not exist (404 / DNS).

    Returns ``(problems, checked)`` where ``problems`` is a list of
    ``{severity, where, url, issue}`` and ``checked`` is the number of distinct URLs fetched.
    Writes ``<dossier-stem>_source_check.json`` beside the dossier unless ``write`` is False.
    (A single fixed name would collide: research.json and deep_research.json share a directory.)
    """
    import urllib.error
    import urllib.request

    doc = _read_json(path) or {}
    ref_date = None
    if isinstance(doc.get("run_date"), str):
        try:
            ref_date = datetime.date.fromisoformat(doc["run_date"])
        except ValueError:
            ref_date = None

    facts = []
    for group in ("lead_candidates", "secondary_items"):
        for i, c in enumerate(doc.get(group) or []):
            if not isinstance(c, dict):
                continue
            for j, fact in enumerate(c.get("important_facts") or []):
                if isinstance(fact, dict) and fact.get("source_url"):
                    facts.append((f"{group}[{i}].important_facts[{j}]",
                                  fact["source_url"], fact.get("quote", "")))

    problems, results = [], []
    page_cache: dict = {}
    for where, url, quote in facts:
        late = freshness.url_date_tokens_after(url, ref_date) if ref_date else None
        if late:
            p = {"severity": "error", "where": where, "url": url,
                 "issue": f"future-dated source URL ({late} > run {ref_date}) — cannot exist yet"}
            problems.append(p)
            results.append({**p, "status": "future_dated"})
            continue

        if url not in page_cache:
            page_cache[url] = _fetch_page(url, timeout, urllib)
        status, page = page_cache[url]

        if status == "notfound":
            issue = "source does not exist (404 / DNS failure)"
            sev, st = "error", "unreachable"
        elif status == "blocked":
            issue = "could not verify (blocked, paywalled, timed out, or JS-rendered)"
            sev, st = "advisory", "unverified"
        else:  # ok
            if _quote_needle(quote) and _quote_needle(quote) in page:
                sev, st, issue = None, "quote_found", ""
            else:
                issue = "verbatim quote not found on page (possible paraphrase/JS/paywall — verify)"
                sev, st = "advisory", "quote_not_found"

        rec = {"severity": sev, "where": where, "url": url, "issue": issue, "status": st}
        results.append(rec)
        if sev:
            problems.append({"severity": sev, "where": where, "url": url, "issue": issue})

    if write:
        out = source_check_path(path)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"path": path, "checked": len(page_cache), "results": results},
                      f, indent=2, ensure_ascii=False)
            f.write("\n")
    return problems, len(page_cache)


def _fetch_page(url: str, timeout: float, urllib) -> tuple:
    """Return ``(status, normalized_text)`` where status is 'ok' | 'notfound' | 'blocked'.
    'notfound' is reserved for definitive non-existence (404 / DNS) — the only network
    outcome allowed to hard-fail a source."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; CautiousOptimismBriefings/1.0; +sourcecheck)",
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2_000_000).decode(resp.headers.get_content_charset() or "utf-8",
                                               errors="replace")
        return "ok", _strip_html(raw)
    except urllib.error.HTTPError as e:
        return ("notfound" if e.code == 404 else "blocked"), ""
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", "")).lower()
        dns = any(s in reason for s in ("name or service not known", "getaddrinfo",
                                        "nodename nor servname", "no address associated",
                                        "name resolution"))
        return ("notfound" if dns else "blocked"), ""
    except Exception:
        return "blocked", ""


def _run_started_from_artifact(path: str):
    """Best-effort: locate runs/<date>/run.json for an artifact at runs/<date>/<id>/<file> and
    return its ``run_started_at`` as an aware datetime, or None. Lets the validator apply the
    pre-release embargo gate without the caller having to pass the run clock explicitly."""
    run_json = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(path))), "run.json")
    try:
        with open(run_json, encoding="utf-8") as f:
            started = json.load(f).get("run_started_at")
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(started, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(started)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def validate_file(kind: str, path: str, write: bool = True) -> list:
    """Validate the artifact at ``path`` as ``kind``. Returns a list of problems."""
    if kind not in _VALIDATORS and kind not in _TEXT_KINDS:
        raise ValueError(f"kind must be one of {list(VALIDATE_KINDS)}")
    if kind == "script":
        # Read-only: resume walks call this on every prompt, so it must not write side-files there.
        return validate_script_file(path, write=write)
    if not os.path.exists(path):
        return [f"file not found: {path}"]
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e}"]
    if not isinstance(doc, dict):
        return ["top level must be a JSON object"]
    if kind in ("research", "deep"):
        return validate_research(doc, run_now=_run_started_from_artifact(path))
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
    # Stage 5 applies to the synthesis family too: the Throughline and Forward Curve have no
    # research or plan gate at all, and the Throughline is the archive's worst listenability
    # offender — it is the LAST prompt that should skip a fresh read.
    final_check = ("final_check", [j(FINAL_CHECK_FILE)], "final_check")
    if kind in SYNTHESIS_KINDS:
        return [("write", [j(DRAFT_FILE)], "script"), review, final_check]
    return [
        ("research", [j(RESEARCH_FILE)], "research"),
        ("plan", [j(PLAN_FILE)], "plan"),
        ("deep", [j(DEEP_FILE)], "deep"),
        ("write", [j(DRAFT_FILE)], "script"),
        review,
        final_check,
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
            problems = validate_file(vkind, paths[0], write=False)
            if problems:
                resume, cut = stage, idx
                reason = f"{os.path.basename(paths[0])} invalid: {problems[0]}"
                break

        # A review that did not approve ends the prompt: there is no script for the final reader
        # to read, so without this the walk would point at final_check forever.
        if stage == "review":
            review_doc = _read_json(paths[0]) or {}
            if review_doc.get("decision") != "approve":
                resume, cut = "finalize", len(chain)
                reason = (f'review decision is {review_doc.get("decision")!r}, not "approve" — '
                          "mark the prompt, do not final-check it")
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

def record_revision(prompt_id: str, date: str, stage: str = "final_check",
                   budget: int = DEFAULT_REVISION_BUDGET) -> dict:
    """Charge one revision against ``prompt_id``'s budget and report whether it is now exhausted.

    Kept in run.json rather than in the artifact so it survives ``resume --prune`` and the chunked
    phase-1 sessions — the two things that would otherwise reset it and let a prompt ping-pong
    between the Reviewer and the final reader indefinitely.
    """
    state = load_state(date)
    entry = _find_entry(state, prompt_id)
    revisions = entry.setdefault("revisions", {})
    count = int(revisions.get(stage, 0)) + 1
    revisions[stage] = count
    _save_state(date, state)
    out = {"id": prompt_id, "stage": stage, "count": count, "budget": budget,
           "exhausted": count >= budget}
    log.info("revision %s/%s charged to %s (%s)", count, budget, prompt_id, stage)
    return out


def final_check_status(date: str, prompt_id: str) -> dict:
    """State of ``final_check.json`` for one prompt: present / fresh / valid / verdict."""
    pdir = prompt_dir(date, prompt_id)
    path = os.path.join(pdir, FINAL_CHECK_FILE)
    final_path = os.path.join(pdir, FINAL_FILE)
    out = {"path": path, "present": os.path.exists(path), "stale": False,
           "problems": [], "verdict": None}
    if not out["present"]:
        return out
    # Stale = older than the script it judges. A reviewer revision must be re-read, not inherited.
    try:
        out["stale"] = os.path.getmtime(path) < os.path.getmtime(final_path) - _MTIME_SLACK
    except OSError:
        pass
    out["problems"] = validate_file("final_check", path)
    out["verdict"] = (_read_json(path) or {}).get("verdict")
    return out


def approve(prompt_id: str, date: str) -> str:
    """Copy runs/<date>/<id>/final.txt to briefings/<id>.txt — the single gate between the
    pipeline and TTS/publishing. Returns the briefing path. Four checks, cheapest first, each
    naming itself on refusal so an unattended failure is diagnosable from the log alone:

    1. ``review.json`` validates and says ``decision: "approve"``
    2. ``final.txt`` exists and is non-empty
    3. ``final.txt`` passes the deterministic script gate (format, leakage, word floor)
    4. ``final_check.json`` is present, fresh, valid, and its verdict is ``pass``

    3 and 4 exist because ``final.txt`` is the *reviewer's own rewrite*: without them the only
    thing standing between a draft and the podcast is an agent grading its own prose.
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

    # The shipped script itself was never checked by anything but the reviewer's prose judgement —
    # and the reviewer wrote it. Format, leakage and word floor are arithmetic, so they gate here.
    problems = validate_script_file(final_path, stage="final")
    if problems:
        raise RuntimeError(f"approve refused — {FINAL_FILE} failed the script gate: "
                           f"{'; '.join(problems)}")

    # The final reader is the only agent that judges the SHIPPED script without having written any
    # of it. Its verdict gates the copy, or the separation it exists to provide is decorative.
    fc = final_check_status(date, prompt_id)
    if not fc["present"]:
        raise RuntimeError(f"approve refused — missing {FINAL_CHECK_FILE}; run the final-reader "
                           f"agent over {FINAL_FILE} first")
    if fc["stale"]:
        raise RuntimeError(f"approve refused — {FINAL_CHECK_FILE} is older than {FINAL_FILE}; "
                           "the script changed after it was read, so re-run the final-reader")
    if fc["problems"]:
        raise RuntimeError(f"approve refused — {FINAL_CHECK_FILE} invalid: "
                           f"{'; '.join(fc['problems'])}")
    if fc["verdict"] != "pass":
        raise RuntimeError(f'approve refused — final-reader verdict is {fc["verdict"]!r}, '
                           'not "pass"')

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
    p.add_argument("--now", default=None,
                   help="run as-of timestamp (ISO-8601 with offset) for the freshness gate; "
                        "defaults to the current time. Preserved across idempotent re-inits.")

    p = sub.add_parser("validate", help="schema-check a pipeline artifact")
    p.add_argument("kind", choices=list(VALIDATE_KINDS))
    p.add_argument("path")

    p = sub.add_parser("verify-sources",
                       help="fetch each figure-bearing source in a research/deep dossier and "
                            "confirm the verbatim quote is on the page (best-effort)")
    p.add_argument("path", help="path to research.json or deep_research.json")
    p.add_argument("--no-write", action="store_true",
                   help="do not write <stem>_source_check.json alongside the dossier")

    p = sub.add_parser("approve", help="copy final.txt to briefings/ if the review approves")
    p.add_argument("prompt_id")
    p.add_argument("--date", default=today)

    p = sub.add_parser("mark", help="record a skipped/failed outcome for a prompt")
    p.add_argument("prompt_id")
    p.add_argument("--date", default=today)
    p.add_argument("--status", choices=MARK_STATUSES, required=True)
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--reason", default="")

    p = sub.add_parser("revision", help="charge one revision against a prompt's budget "
                                       "(exit 3 = budget exhausted, skip instead of retrying)")
    p.add_argument("prompt_id")
    p.add_argument("--date", default=today)
    p.add_argument("--stage", default="final_check", choices=STAGES)
    p.add_argument("--budget", type=int, default=DEFAULT_REVISION_BUDGET)

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
        plan = init_run(args.date, args.novelty, now_iso=args.now)
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "verify-sources":
        problems, checked = verify_sources(args.path, write=not args.no_write)
        hard = [p for p in problems if p["severity"] == "error"]
        for p in problems:
            print(f"  [{p['severity'].upper()}] {p['where']}: {p['issue']}")
        print(f"{'FAIL' if hard else 'OK'} verify-sources: {args.path} "
              f"({checked} source(s) checked, {len(hard)} hard, "
              f"{len(problems) - len(hard)} advisory)")
        return 1 if hard else 0

    if args.cmd == "validate":
        problems = validate_file(args.kind, args.path)
        if args.kind == "script":
            # Advisories carry the listenability signal while it is still warn-only — print them
            # even on a pass, or the whole warn phase would be invisible.
            doc = _read_json(script_check_path(args.path)) or {}
            for prob in doc.get("problems") or []:
                if prob.get("severity") != "error":
                    print(f"  [ADVISORY] {prob.get('code')}: {prob.get('detail')}")
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

    if args.cmd == "revision":
        try:
            out = record_revision(args.prompt_id, args.date, args.stage, args.budget)
        except (FileNotFoundError, KeyError) as e:
            print(str(e))
            return 1
        print(f"revision {out['count']}/{out['budget']} for {out['id']} ({out['stage']})")
        if out["exhausted"]:
            # A distinct exit code so the session can branch without parsing prose.
            print("budget exhausted — mark the prompt skipped rather than revising again")
            return 3
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
