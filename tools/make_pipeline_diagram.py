"""Render docs/pipeline-agent-flow.png — the briefing pipeline's agent flow and responsibilities.

Standalone documentation art, not part of the publishing pipeline. Re-run after changing a stage:

    python tools/make_pipeline_diagram.py

Note it runs on the **base** conda env, not `Spotify` — matplotlib is only installed in base, and
this script is documentation tooling with no place in the publishing env's dependencies.

Colour roles come from the dataviz reference palette and encode the KIND of agent (web research /
judgment / writing / deterministic code gate). Colour is never the sole carrier: every card also
states its model and web access in text, and the optional stage is dashed as well as labelled.
The axes deliberately fills the whole figure so that text wrapping can be computed in points.
"""
import os
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "pipeline-agent-flow.png")

FIG_W, FIG_H = 23.0, 16.0
XUNIT = FIG_W * 72 / 100.0   # points per x-unit, with the axes filling the figure
CHAR = 0.545                 # DejaVu Sans average advance width, in em, for mixed-case text

# --- palette (dataviz reference instance, light surface) ---------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
HAIRLINE = "#e1e0d9"

WEB = "#2a78d6"       # web-research agents
JUDGE = "#4a3aa7"     # judgment agents (no web)
WRITE = "#008300"     # writing agent (no web)
GATE = "#52514e"      # deterministic code, not an agent (neutral by design)

FONT = "DejaVu Sans"


def fits(width_units, fontsize):
    """How many characters of `fontsize` fit across `width_units` x-units."""
    return max(8, int(width_units * XUNIT / (CHAR * fontsize)))


def tint(hex_color, amount=0.93):
    """Blend a hue toward the surface for a card body fill."""
    c = tuple(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    s = tuple(int(SURFACE[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return tuple(c[i] + (s[i] - c[i]) * amount for i in range(3))


# --- stage content -----------------------------------------------------------
STAGES = [
    {
        "tag": "STAGE 1", "name": "RESEARCHER", "color": WEB, "optional": False,
        "badges": ["sonnet", "WEB ACCESS"],
        "reads": "the standing prompt; CLAUDE.md source hierarchy",
        "writes": "research.json",
        "does": [
            "Sweeps the last few days for the strongest material, primary sources first.",
            "Captures exact dates, figures, and deltas versus consensus.",
            "Every figure-bearing fact carries a VERBATIM QUOTE — no downstream agent "
            "has web access, so an unquoted number is unpublishable.",
            "Sorts thin or unverified material into uncertainties and items_to_ignore.",
            "May declare status: insufficient rather than pad the dossier.",
        ],
        "never": "Never picks the thesis, judges novelty, or writes the script.",
    },
    {
        "tag": "STAGE 2", "name": "ANALYST-EDITOR", "color": JUDGE, "optional": False,
        "badges": ["opus", "no web"],
        "reads": "research.json; the prior briefing; 5 days of this topic's transcripts "
                 "plus 5 Throughlines",
        "writes": "editorial_plan.json",
        "does": [
            "Decides WRITE or SKIP — a skipped day beats a padded episode.",
            "Chooses the items and their weight (lead / major / brief), the central "
            "thesis, and the running order.",
            "Applies the novelty mode: strict on the 5AM job, relaxed interactively.",
            "Detects emergent arcs across the 5-day lookback.",
            "NEW: audits its own plan for evidence gaps and may commission a deep dive.",
        ],
        "never": "Never touches the web or writes prose.",
    },
    {
        "tag": "STAGE 2.5   ·   OPTIONAL", "name": "DEEP-RESEARCHER", "color": WEB,
        "optional": True,
        "badges": ["sonnet", "WEB ACCESS", "on request only"],
        "reads": "the plan's deep_dive_requests; research.json",
        "writes": "deep_research.json — same schema as the dossier",
        "does": [
            "Answers at most 3 named questions about ONE already-approved item.",
            "Spends at most 6 web calls; prefers one authoritative primary document "
            "over three secondary ones.",
            "Same verbatim-quote contract, so its figures are fully publishable.",
            "Records contradictions if the evidence undercuts the frozen plan.",
            "May return insufficient — telling the writer to DROP the claim rather "
            "than hedge around it.",
        ],
        "never": "Never proposes new stories or reopens the editorial plan.",
    },
    {
        "tag": "STAGE 3", "name": "WRITER", "color": WRITE, "optional": False,
        "badges": ["sonnet", "no web"],
        "reads": "research.json; editorial_plan.json; deep_research.json if it exists",
        "writes": "draft.txt",
        "does": [
            "Turns the plan into a spoken script at the prompt's stated word count.",
            "May use ONLY figures carrying a verbatim quote in its input files.",
            "One spine, signposted transitions, one idea per sentence.",
            "Must honour any contradictions the deep dive recorded.",
        ],
        "never": "Never introduces a fact absent from its inputs; never writes "
                 "review.json or final.txt.",
    },
    {
        "tag": "STAGE 4", "name": "REVIEWER", "color": JUDGE, "optional": False,
        "badges": ["opus", "no web", "fresh context"],
        "reads": "draft.txt; research.json; the plan; deep_research.json; transcripts",
        "writes": "review.json  +  final.txt",
        "does": [
            "Independent editor — it did NOT write the draft it is judging.",
            "Audits EVERY figure against the verbatim quotes; unsupported numbers are "
            "cut or explicitly hedged.",
            "Verifies cross-day callbacks against the actual transcripts.",
            "Checks that a commissioned deep dive was actually used.",
            "Revises once, then decides approve / skip / failed.",
        ],
        "never": "Approve is not its default outcome. Never researches; never copies "
                 "into briefings/.",
    },
]

HANDOFFS = ["research.json", "editorial_plan.json", "deep_research.json", "draft.txt"]

PANELS = [
    ("THE GATE:  orchestrator.py   (deterministic code, not an agent)", GATE, [
        "Stdlib CLI the session calls between stages. The agents propose; this disposes.",
        "• init — per-prompt run dirs + run.json. Idempotent, so a batch resumes.",
        "• validate research | plan | deep | review — schema checks. \"deep\" is an alias for "
        "the research checker, so the quote contract is enforced on deep-dive facts by the "
        "very same code.",
        "• validate plan bounds the request: ≤1 item, ≤3 questions, must name an approved item.",
        "• approve — the ONLY path that copies final.txt into briefings/<id>.txt, and only when "
        "review.json says \"approve\". A rejected script cannot reach TTS.",
        "• mark — records a skip or failure. One bad prompt never stops the batch.",
    ]),
    ("WHEN DOES STAGE 2.5 ACTUALLY RUN?", WEB, [
        "Whenever the Analyst-Editor leaves a non-empty deep_dive_requests — in EVERY novelty "
        "mode, including the unattended 5AM job that publishes to Spotify. A quality stage "
        "gated out of the run that publishes would improve nothing.",
        "",
        "When the list is empty the pipeline goes straight from plan to draft. That is the "
        "common case and it costs nothing.",
        "If the stage fails, or its output will not validate after one repair attempt, "
        "deep_research.json is deleted and the Writer runs anyway — the stage is an "
        "enhancement and can never fail a prompt.",
        "Budget: ≈ +12% tokens on a prompt that uses it, ≈ +5–7% on the batch. If the 5AM run "
        "starts brushing its usage cap, trim stage 1 rather than re-gating this stage.",
    ]),
    ("WHY STAGE 2.5 EXISTS", WEB, [
        "Measured on the 2026-07-23 run:",
        "• 7 of 10 drafts landed 940–1,020 words against a 1,200–1,500 floor, with "
        "plan-required second-order effects left undeveloped.",
        "• 5 of 10 carried figures supported only by dossier summary prose, so the reviewer "
        "hedged them (\"reported near $17.9 billion\") or cut them.",
        "",
        "The Analyst-Editor demands arguments the dossier cannot support, and the Writer has no "
        "way to go get the evidence. Stage 2.5 closes that loop at the one point where the gap "
        "is known and specific.",
        "Cost ≈ +12% tokens on a prompt that uses it; empty requests cost nothing.",
    ]),
]


def flow(ax, x, y, text, width_units, size, color, leading, weight="normal",
         style="normal", draw=True):
    """Wrap `text` and advance y downward. With draw=False, measure only.

    Measuring and drawing share this one code path, so the layout pass below can size the
    cards and panels to their actual content instead of hand-tuned constants.
    """
    lines = textwrap.wrap(text, fits(width_units, size)) or [""]
    for line in lines:
        if draw:
            ax.text(x, y, line, fontsize=size, color=color, family=FONT,
                    va="top", fontweight=weight, style=style)
        y -= leading
    return y


def card_body(ax, st, x0, w, top, head_h, draw=True):
    """Render (or measure) a stage card's body. Returns the y where its content ends."""
    pad = 0.9
    body_w = w - pad * 2
    tx = x0 + pad
    y = top - head_h - 1.4

    bx = x0 + pad
    for b in st["badges"]:
        bw = len(b) * 0.40 + 1.2
        if draw:
            ax.add_patch(FancyBboxPatch((bx, y - 1.5), bw, 1.5,
                                        boxstyle="round,pad=0.05,rounding_size=0.35",
                                        facecolor="#ffffff", edgecolor=st["color"],
                                        linewidth=1.0, zorder=4))
            ax.text(bx + bw / 2, y - 0.75, b, fontsize=8.0, color=st["color"], family=FONT,
                    ha="center", va="center", fontweight="bold", zorder=5)
        bx += bw + 0.5
    y -= 3.0

    if draw:
        ax.text(tx, y, "READS", fontsize=8.2, color=MUTED, family=FONT, va="top",
                fontweight="bold")
    y = flow(ax, tx, y - 1.6, st["reads"], body_w, 9.0, INK2, 1.42, draw=draw)
    y -= 0.8
    if draw:
        ax.text(tx, y, "WRITES", fontsize=8.2, color=MUTED, family=FONT, va="top",
                fontweight="bold")
    y = flow(ax, tx, y - 1.6, st["writes"], body_w, 9.0, INK, 1.42, weight="bold", draw=draw)
    y -= 1.1
    if draw:
        ax.plot([tx, x0 + w - pad], [y + 0.3, y + 0.3], color=HAIRLINE, linewidth=1.1)
    y -= 0.6
    if draw:
        ax.text(tx, y, "RESPONSIBILITIES", fontsize=8.2, color=MUTED, family=FONT,
                va="top", fontweight="bold")
    y -= 1.8
    for item in st["does"]:
        if draw:
            ax.text(tx, y, "•", fontsize=9.2, color=st["color"], family=FONT, va="top",
                    fontweight="bold")
        y = flow(ax, tx + 1.05, y, item, body_w - 1.05, 9.2, INK, 1.48, draw=draw)
        y -= 0.5
    return y


def never_height(st, w):
    """Height the pinned italic 'never' footer needs, in y-units."""
    return 0.7 + len(textwrap.wrap(st["never"], fits(w - 1.8, 8.8))) * 1.4


def panel_height(pw, lines):
    """Height a bottom panel needs for its heading + body, in y-units."""
    h = 3.5
    for line in lines:
        if not line:
            h += 0.9
            continue
        width = pw - 3.5 if line.startswith("• ") else pw - 2.6
        body = line[2:] if line.startswith("• ") else line
        h += len(textwrap.wrap(body, fits(width, 8.8))) * 1.36 + 0.25
    return h + 1.0


def main():
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_position([0, 0, 1, 1])          # axes fills the figure, so XUNIT is exact
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    # --- title ---------------------------------------------------------------
    ax.text(3, 97.8, "Cautious Optimism Briefings — pipeline agent flow",
            fontsize=26, fontweight="bold", color=INK, family=FONT, va="top")
    ax.text(3, 94.6,
            "Five separated subagents with file handoffs under runs/<date>/<prompt_id>/. Each has "
            "its own fresh context — they communicate only through files on disk, never directly.",
            fontsize=12, color=INK2, family=FONT, va="top")

    # --- legend --------------------------------------------------------------
    lx = 3
    for color, label, dashed in ((WEB, "web research", False),
                                 (JUDGE, "judgment · no web", False),
                                 (WRITE, "writing · no web", False),
                                 (GATE, "deterministic code gate", False),
                                 (WEB, "dashed = optional stage", True)):
        ax.add_patch(FancyBboxPatch((lx, 91.0), 1.4, 1.0,
                                    boxstyle="round,pad=0.08,rounding_size=0.25",
                                    facecolor="none" if dashed else color, edgecolor=color,
                                    linewidth=1.6 if dashed else 0,
                                    linestyle=(0, (2.2, 1.6)) if dashed else "solid"))
        ax.text(lx + 2.0, 91.5, label, fontsize=10.5, color=INK2, family=FONT, va="center")
        lx += len(label) * 0.60 + 5.8

    # --- geometry ------------------------------------------------------------
    n = len(STAGES)
    left, right, gap = 3.0, 97.0, 1.6
    w = (right - left - gap * (n - 1)) / n
    top = 84.0
    centers = [left + i * (w + gap) + w / 2 for i in range(n)]

    # --- handoff rail --------------------------------------------------------
    for i, label in enumerate(HANDOFFS):
        x0, x1 = centers[i], centers[i + 1]
        ax.add_patch(FancyArrowPatch((x0 + w * 0.17, 86.4), (x1 - w * 0.17, 86.4),
                                     arrowstyle="-|>", mutation_scale=16,
                                     linewidth=1.6, color=MUTED, shrinkA=0, shrinkB=0))
        ax.text((x0 + x1) / 2, 87.1, label, fontsize=10.5, color=INK2, family=FONT,
                ha="center", va="bottom", fontweight="bold")

    # --- measure, then place --------------------------------------------------
    # Cards are sized to their tallest content, and the panels to theirs; whatever is left
    # over becomes the bypass-arc band. Nothing below is a hand-tuned vertical constant.
    head_h = 6.0
    pw = (right - left - gap * 2) / 3
    content_ends = [card_body(ax, st, centers[i] - w / 2, w, top, head_h, draw=False)
                    for i, st in enumerate(STAGES)]
    bottom = min(end - never_height(st, w) - 0.8
                 for end, st in zip(content_ends, STAGES))
    pbot = 1.8
    ptop = pbot + max(panel_height(pw, lines) for _, _, lines in PANELS)
    arc_label_y = (ptop + bottom) / 2

    # --- cards ---------------------------------------------------------------
    for st, cx in zip(STAGES, centers):
        x0 = cx - w / 2
        dashed = (0, (3.2, 2.2)) if st["optional"] else "solid"
        ax.add_patch(FancyBboxPatch((x0, bottom), w, top - bottom,
                                    boxstyle="round,pad=0,rounding_size=0.6",
                                    facecolor=tint(st["color"]), edgecolor=st["color"],
                                    linewidth=1.9, linestyle=dashed, zorder=2))
        ax.add_patch(FancyBboxPatch((x0, top - head_h), w, head_h,
                                    boxstyle="round,pad=0,rounding_size=0.6",
                                    facecolor=st["color"], edgecolor="none", zorder=3))
        ax.add_patch(plt.Rectangle((x0, top - head_h), w, 0.9, facecolor=st["color"],
                                   edgecolor="none", zorder=3))
        ax.text(cx, top - 1.9, st["tag"], fontsize=9.3, color="#ffffff", family=FONT,
                ha="center", va="center", fontweight="bold", alpha=0.93, zorder=4)
        ax.text(cx, top - 4.1, st["name"], fontsize=14.5, color="#ffffff", family=FONT,
                ha="center", va="center", fontweight="bold", zorder=4)

        card_body(ax, st, x0, w, top, head_h, draw=True)

        ny = bottom + 0.7
        for line in reversed(textwrap.wrap(st["never"], fits(w - 1.8, 8.8))):
            ax.text(x0 + 0.9, ny, line, fontsize=8.8, color=INK2, family=FONT, va="bottom",
                    style="italic")
            ny += 1.4

    # --- bypass arc (the default path skips 2.5) -----------------------------
    ax.add_patch(FancyArrowPatch((centers[1], bottom - 0.4), (centers[3], bottom - 0.4),
                                 connectionstyle="arc3,rad=0.40", arrowstyle="-|>",
                                 mutation_scale=17, linewidth=1.9, color=MUTED,
                                 linestyle=(0, (4, 2.4)), shrinkA=2, shrinkB=2, zorder=1))
    ax.text(centers[2], arc_label_y,
            "DEFAULT PATH — when deep_dive_requests is empty, stage 2.5 never runs "
            "and the plan goes straight to the Writer",
            fontsize=11, color=INK2, family=FONT, ha="center", va="center", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.55", facecolor=SURFACE, edgecolor=HAIRLINE,
                      linewidth=1.2), zorder=5)

    # --- bottom panels -------------------------------------------------------
    for i, (heading, accent, lines) in enumerate(PANELS):
        px = left + i * (pw + gap)
        ax.add_patch(FancyBboxPatch((px, pbot), pw, ptop - pbot,
                                    boxstyle="round,pad=0,rounding_size=0.6",
                                    facecolor="#ffffff", edgecolor=HAIRLINE, linewidth=1.4))
        ax.add_patch(plt.Rectangle((px, pbot), 0.42, ptop - pbot, facecolor=accent,
                                   edgecolor="none"))
        hx = px + 1.5
        ax.text(hx, ptop - 1.2, heading, fontsize=10.5, color=INK, family=FONT,
                va="top", fontweight="bold")
        y = ptop - 3.5
        for line in lines:
            if not line:
                y -= 0.9
                continue
            if line.startswith("• "):
                ax.text(hx, y, "•", fontsize=8.8, color=accent, family=FONT, va="top",
                        fontweight="bold")
                y = flow(ax, hx + 1.0, y, line[2:], pw - 3.5, 8.8, INK2, 1.36)
                y -= 0.25
            else:
                y = flow(ax, hx, y, line, pw - 2.6, 8.8, INK2, 1.36)
                y -= 0.25

    fig.savefig(OUT, dpi=150, facecolor=SURFACE)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
