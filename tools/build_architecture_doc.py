"""Build the printable agent-architecture document.

WHY THIS EXISTS
---------------
The source document draws its diagrams with Mermaid, rendered in the browser by JavaScript.
That prints BLANK. The reason is specific and worth recording: Mermaid v11 puts flowchart and
state-diagram node labels inside an SVG ``<foreignObject>``, and **Chrome's print/PDF engine does
not render foreignObject at all** — you get the diagram's frame and nothing inside it. Setting
``htmlLabels: false`` does not help; v11 ignores it for the flowchart renderer.

So this script renders each diagram once with headless Chrome, screenshots it to a PNG, and bakes
that PNG into the page as a data URI. An ``<img>`` always prints. The outputs are static, offline
and print-safe:

    agent-architecture.html   no JavaScript, no CDN, diagrams are embedded PNG
    agent-architecture.pdf    A4 portrait, hand this to the printer

Edit ``tools/agent-architecture.src.html``, then re-run:

    python tools/build_architecture_doc.py

Outputs go to the REPO ROOT, deliberately not ``docs/`` — ``publish_feed.py`` runs ``git add docs``,
so anything there is auto-committed and served publicly on the podcast's Pages site.
"""
import base64
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
SRC = os.path.join(HERE, "agent-architecture.src.html")
OUT_HTML = os.path.join(PROJ, "agent-architecture.html")
OUT_PDF = os.path.join(PROJ, "agent-architecture.pdf")

# v10, NOT v11. v11 clips long flowchart labels; v10 sizes them correctly. Both use an
# SVG <foreignObject>, which Chrome's PDF engine refuses to draw - which is exactly why
# this script rasterises each diagram in a browser instead of shipping the SVG.
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs"
PLATE_BG = (240, 244, 246)          # --plate-bg, the colour we crop away
SHOT_W, SHOT_H = 2600, 2600         # generous canvas; the diagram is cropped out of it
SCALE = 2                           # device pixel ratio -> ~420 dpi at A4 text width

CHROME_CANDIDATES = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        p = os.path.expandvars(c)
        if os.path.exists(p):
            return p
    raise SystemExit("No Chrome or Edge found — needed to render the diagrams.")


def run_chrome(chrome: str, args: list, profile: str) -> None:
    subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         f"--user-data-dir={profile}", *args],
        check=False, capture_output=True,
    )


def _natural_size(code: str) -> str:
    """Force useMaxWidth:false into the diagram's own init directive.

    Per-diagram ``%%{init}%%`` directives override ``mermaid.initialize()``, so the setting has to
    go inside the directive or it is silently ignored."""
    if "'flowchart':{" in code:
        code = code.replace("'flowchart':{", "'flowchart':{'useMaxWidth':false,", 1)
    else:
        code = code.replace("%%{init: {", "%%{init: {'flowchart':{'useMaxWidth':false},", 1)
    return code.replace(
        "%%{init: {",
        "%%{init: {'sequence':{'useMaxWidth':false},'gantt':{'useMaxWidth':false},"
        "'state':{'useMaxWidth':false},", 1)


def shoot(chrome: str, code: str, workdir: str, idx: int) -> bytes:
    """Render one mermaid block and return a tightly-cropped PNG."""
    from PIL import Image

    page = os.path.join(workdir, f"d{idx}.html")
    shot = os.path.join(workdir, f"d{idx}.png")
    with open(page, "w", encoding="utf-8") as f:
        f.write(
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            f"html,body{{margin:0;padding:14px;background:rgb{PLATE_BG};}}"
            "#d{display:block;width:max-content;}"
            "</style></head><body><div id='d'><pre class='mermaid'>"
            + html.escape(_natural_size(code), quote=False) +
            "</pre></div><script type='module'>"
            f"import mermaid from '{MERMAID_CDN}';"
            "mermaid.initialize({startOnLoad:true,securityLevel:'loose'});"
            "</script></body></html>"
        )

    run_chrome(chrome, [
        f"--window-size={SHOT_W},{SHOT_H}",
        f"--force-device-scale-factor={SCALE}",
        "--virtual-time-budget=20000",
        "--run-all-compositor-stages-before-draw",
        f"--screenshot={shot}",
        "file:///" + page.replace("\\", "/"),
    ], os.path.join(workdir, f"p{idx}"))

    if not os.path.exists(shot):
        raise SystemExit(f"diagram {idx}: Chrome produced no screenshot")

    img = Image.open(shot).convert("RGB")
    # Crop away the flat background so each figure sits tight in the page.
    bg = Image.new("RGB", img.size, PLATE_BG)
    from PIL import ImageChops
    bbox = ImageChops.difference(img, bg).convert("L").point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bbox:
        raise SystemExit(f"diagram {idx}: rendered blank — Mermaid probably failed to load")
    pad = 10 * SCALE
    bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
            min(img.width, bbox[2] + pad), min(img.height, bbox[3] + pad))
    img = img.crop(bbox)

    png = os.path.join(workdir, f"c{idx}.png")
    img.save(png, "PNG", optimize=True)
    with open(png, "rb") as f:
        return f.read()


def main() -> int:
    chrome = find_chrome()
    print(f"browser : {chrome}")
    print(f"source  : {SRC}")

    with open(SRC, encoding="utf-8") as f:
        src = f.read()

    blocks = re.findall(r'<pre class="mermaid">(.*?)</pre>', src, re.S)
    print(f"diagrams: {len(blocks)}")
    if not blocks:
        raise SystemExit("no mermaid blocks found in the source")

    work = os.path.join(tempfile.gettempdir(), "archdoc-" + uuid.uuid4().hex)
    os.makedirs(work, exist_ok=True)
    try:
        images = []
        for i, code in enumerate(blocks, 1):
            data = shoot(chrome, html.unescape(code).strip(), work, i)
            images.append(data)
            print(f"  fig {i}: {len(data):>9,} bytes PNG")

        # Swap each mermaid block for its rendered image.
        it = iter(images)

        def sub(_m):
            data = next(it)
            b64 = base64.b64encode(data).decode("ascii")
            return (f'<img class="figure" alt="Figure rendered from the Mermaid source" '
                    f'src="data:image/png;base64,{b64}">')

        out = re.sub(r'<pre class="mermaid">.*?</pre>', sub, src, flags=re.S)

        # No JS, no CDN, no fallback styling for a <pre> that no longer exists.
        out = re.sub(r'(?is)<script[^>]*type="module"[^>]*>.*?</script>', "", out)
        out = out.replace(
            ".plate pre.mermaid {",
            ".plate img.figure { display:block; width:100%; height:auto; margin:0 auto; }\n"
            "  .plate pre.mermaid {")
        out = out.replace(
            "    .plate svg { max-width: 100% !important; height: auto !important; }",
            "    .plate img.figure { max-width: 100% !important; height: auto !important; }")
        out = out.replace(
            "  Diagrams are Mermaid and load the renderer from a CDN, so the first render needs\n"
            "  an internet connection; offline, each diagram degrades to readable source text.",
            "  GENERATED FILE — do not edit. Build with: python tools/build_architecture_doc.py\n"
            "  Diagrams are pre-rendered PNGs embedded as data URIs, so the page needs no network\n"
            "  and no JavaScript, and prints correctly (Chrome's PDF engine drops SVG foreignObject,\n"
            "  which is what Mermaid uses for flowchart labels — hence rasterising them here).")

        with open(OUT_HTML, "w", encoding="utf-8", newline="") as f:
            f.write(out)
        print(f"static html : {OUT_HTML} ({os.path.getsize(OUT_HTML):,} bytes)")

        run_chrome(chrome, [
            "--virtual-time-budget=10000",
            "--no-pdf-header-footer",
            f"--print-to-pdf={OUT_PDF}",
            "file:///" + OUT_HTML.replace("\\", "/"),
        ], os.path.join(work, "pdf"))
        if not os.path.exists(OUT_PDF):
            raise SystemExit("Chrome did not produce a PDF")
        print(f"pdf         : {OUT_PDF} ({os.path.getsize(OUT_PDF):,} bytes)")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
