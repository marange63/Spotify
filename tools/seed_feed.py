"""Seed feed_state.json + docs/feed.xml from the four briefings already rendered
in ./briefings for 2026-07-08, then build the feed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import library
from feed import add_episode, build_feed

DATE = "2026-07-08"
SUMMARIES = {
    "frontier-ai-labs": "GPT-5.6 (Sol/Terra/Luna) previews with a cost attack on Anthropic; Google delays Gemini 3.5 for a rebuild; Anthropic's $965B round; China's open-weight surge and the end of the H20.",
    "new-prompt": "Cross-asset radar into the June FOMC minutes: Warsh's no-dot hawkish hold vs. a soft 57k payrolls print, a 40-year-low yen, tight credit, and a higher-vol rates regime.",
    "new-prompt-2": "Digital money: Bitcoin's macro-driven bounce to ~$64k on thin flows, while GENIUS Act implementation, USDC's share gains, reserve money-market funds, and Open USD rewire the plumbing.",
    "new-prompt-3": "Strategic power: transformers as the binding constraint (DPA finding), AI power demand, China's $295B sovereign compute grid, and the November rare-earth expiry.",
}

data = library.load()
for p in data["prompts"]:
    if not p.get("enabled"):
        continue
    pid = p["id"]
    mp3 = os.path.join("briefings", pid + ".mp3")
    if not os.path.exists(mp3):
        print(f"skip {pid}: no mp3")
        continue
    rec = add_episode(pid, p["name"], SUMMARIES.get(pid, p["name"]), mp3, DATE)
    print("added", rec["guid"], rec["title"])

path = build_feed()
print("built", path)
