"""Download the model files into one folder that the inference service loads.

Usage (in an environment with inference/requirements.txt installed):
    python infra/fetch_models.py [out_dir]      # default: models/ in the repo

Result (paths match infra/model/config.json):
    <out_dir>/config.json                                  copy of infra/model/config.json
    <out_dir>/speciesnet-v4.0.3a/                          SpeciesNet classifier, labels, taxonomy
                                                           and the MegaDetector v5a weights (~505 MB)
    <out_dir>/clip-vit-b-32/open_clip_model.safetensors    CLIP ViT-B/32, OpenAI weights (~600 MB)

Run locally with MODEL_DIR=<out_dir>; the deploy step uploads the same folder
to Cloud Storage. The service then never downloads anything at startup.
"""

import shutil
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download
from speciesnet.utils import ModelInfo

REPO = Path(__file__).resolve().parent.parent
SPECIESNET_MODEL = "kaggle:google/speciesnet/pyTorch/v4.0.3a/1"
CLIP_REPO = "timm/vit_base_patch32_clip_224.openai"  # what open_clip uses for ViT-B-32 "openai"
CLIP_FILE = "open_clip_model.safetensors"


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "models"
    out.mkdir(parents=True, exist_ok=True)

    # ModelInfo downloads the Kaggle files and the MegaDetector weights into
    # one cache folder (or reuses it); copy that whole folder.
    info = ModelInfo(SPECIESNET_MODEL)
    speciesnet_dir = out / "speciesnet-v4.0.3a"
    shutil.copytree(info.classifier.parent, speciesnet_dir, dirs_exist_ok=True)
    print("SpeciesNet ->", speciesnet_dir)

    clip_file = Path(hf_hub_download(CLIP_REPO, CLIP_FILE))
    (out / "clip-vit-b-32").mkdir(exist_ok=True)
    shutil.copy2(clip_file, out / "clip-vit-b-32" / CLIP_FILE)
    print("CLIP       ->", out / "clip-vit-b-32" / CLIP_FILE)

    shutil.copy2(REPO / "infra" / "model" / "config.json", out / "config.json")
    print("config     ->", out / "config.json")


if __name__ == "__main__":
    main()
