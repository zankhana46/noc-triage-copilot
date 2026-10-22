#!/usr/bin/env bash
# Phase 4: QLoRA fine-tuning entry point for a rented GPU. Default target
# is Google Colab's free T4 GPU (works unmodified on RunPod/Lambda/Kaggle
# too — none of this is Colab-specific).
#
# Colab usage:
#   1. Build the dataset LOCALLY first (no GPU needed):
#        uv run --extra dev scripts/04_build_finetune_dataset.py
#   2. Get this repo onto the Colab instance — either push it to GitHub
#      and `!git clone <url>`, or zip it and upload to Drive, then
#      `!unzip ... && cd noc-copilot`. Either way, data/finetune/*.jsonl
#      from step 1 must come along.
#   3. Runtime > Change runtime type > T4 GPU.
#   4. In a cell: !bash scripts/04_finetune.sh
#
# Adapter + merged model land under checkpoints/ and merged_model/ (see
# configs/finetune.yaml) — copy them to Drive before the Colab session
# ends, since its local disk doesn't persist across sessions.

set -euo pipefail
cd "$(dirname "$0")/.."

pip install -q -r requirements-finetune.txt
# Colab preinstalls an old torchao that's incompatible with our pinned
# peft and breaks LoRA merging; we use bitsandbytes for quantization, not
# torchao, so just remove it. `|| true`: harmless if it was never installed
# (e.g. on RunPod/Lambda/Kaggle).
pip uninstall -y torchao || true

python src/noc_copilot/finetune/train.py
python src/noc_copilot/finetune/merge_adapter.py

echo "Done. LoRA adapter -> checkpoints/, merged model -> merged_model/ (see configs/finetune.yaml)."
