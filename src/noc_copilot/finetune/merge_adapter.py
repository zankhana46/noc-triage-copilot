"""Merges the trained LoRA adapter into the base model, producing a
standalone merged model directory that vLLM can serve directly (Phase 5).
Runs on the same GPU box as train.py, right after it.
"""

from __future__ import annotations

from pathlib import Path

import torch
import yaml
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    cfg = yaml.safe_load((REPO_ROOT / "configs" / "finetune.yaml").read_text())
    base_model_id = cfg["base_model"]
    adapter_dir = REPO_ROOT / cfg["training"]["output_dir"]
    merged_dir = REPO_ROOT / cfg["training"]["merged_output_dir"]
    # An 8B model in fp16 (~16GB) may not fully fit in a single GPU's VRAM
    # (e.g. T4's ~15GB); accelerate spills the overflow layers here instead
    # of failing outright.
    offload_dir = REPO_ROOT / "offload_tmp"
    offload_dir.mkdir(parents=True, exist_ok=True)

    if not adapter_dir.exists():
        raise SystemExit(f"{adapter_dir} not found; run train.py first")

    base_model = AutoModelForCausalLM.from_pretrained(
        base_model_id,
        dtype=torch.float16,
        device_map="auto",
        # Capped well under the GPU's real total (not just left to "auto",
        # which packs the GPU to the brim) — merge_and_unload() needs
        # headroom to temporarily bring each offloaded layer back onto
        # GPU one at a time; a fully-packed GPU leaves no room for that.
        max_memory={0: "9GiB", "cpu": "11GiB"},
        offload_folder=str(offload_dir),
    )
    model = PeftModel.from_pretrained(
        base_model, str(adapter_dir), offload_folder=str(offload_dir)
    )
    merged = model.merge_and_unload()

    merged_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(merged_dir))
    AutoTokenizer.from_pretrained(base_model_id).save_pretrained(str(merged_dir))
    print(f"Merged model saved to {merged_dir}")


if __name__ == "__main__":
    main()
