"""Model chaining (Spec 4.1): use SpeciesNet (+Australia rules); when the image's
best SpeciesNet score is below FALLBACK, forward the image to CLIP
(MDv5a boxes + CLIP labels) instead. No model re-run.
Usage: python chain.py [fallback] [source]
  source = speciesnet_rules (one label per image, default) or speciesnet_perbox
  -> results/chain.json or results/chain_perbox.json"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
FALLBACK = float(sys.argv[1]) if len(sys.argv) > 1 else 0.3
SOURCE = sys.argv[2] if len(sys.argv) > 2 else "speciesnet_rules"
sn = json.load(open(os.path.join(HERE, "results", SOURCE + ".json")))
clip = json.load(open(os.path.join(HERE, "results", "md_clip.json")))
name = "chain" if SOURCE == "speciesnet_rules" else "chain_perbox"
out = {**sn, "pipeline": name, "images": {}}
for f, r in sn["images"].items():
    score = max((b["score"] for b in r["boxes"]), default=0)
    use_clip = score < FALLBACK
    t = r["time_s"] + (clip["images"][f]["time_s"] if use_clip else 0)
    out["images"][f] = {"time_s": round(t, 2), "boxes": clip["images"][f]["boxes"] if use_clip else r["boxes"]}
    if use_clip: print("fallback to CLIP:", f, round(score, 2))
json.dump(out, open(os.path.join(HERE, "results", name + ".json"), "w"), indent=1)
