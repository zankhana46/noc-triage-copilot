"""Phase 4 QLoRA fine-tuning on the Diagnostician + Remediation Writer SFT
dataset built by scripts/04_build_finetune_dataset.py.

Runs on a rented GPU (see scripts/04_finetune.sh) — NOT in this repo's main
venv, which doesn't install torch/transformers/peft/trl/bitsandbytes (see
requirements-finetune.txt for why they're kept separate). This module has
no dependency on the rest of noc_copilot; it only reads configs/finetune.yaml
and data/finetune/*.jsonl.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import mlflow
import torch
import yaml
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_config() -> dict[str, Any]:
    return yaml.safe_load((REPO_ROOT / "configs" / "finetune.yaml").read_text())


def _patch_tokenizer_for_non_thinking(tokenizer: Any) -> None:
    """Qwen3 is a hybrid thinking/non-thinking model whose chat template
    injects <think> scaffolding unless enable_thinking=False is passed.
    Patch it in so every call site (including TRL's internal ones) gets
    the non-thinking template without needing to touch TRL's code."""
    original_apply = tokenizer.apply_chat_template

    def patched(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("enable_thinking", False)
        return original_apply(*args, **kwargs)

    tokenizer.apply_chat_template = patched


def main() -> None:
    cfg = load_config()
    base_model = cfg["base_model"]
    qcfg = cfg["quantization"]
    lcfg = cfg["lora"]
    tcfg = cfg["training"]

    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
    mlflow.set_experiment(cfg["mlflow"]["experiment_name"])

    dataset_dir = REPO_ROOT / cfg["dataset"]["out_dir"]
    train_path = dataset_dir / "train.jsonl"
    dev_path = dataset_dir / "dev.jsonl"
    if not train_path.exists():
        raise SystemExit(
            f"{train_path} not found; run "
            "`uv run --extra dev scripts/04_build_finetune_dataset.py` locally first "
            "(no GPU needed) and bring data/finetune/ along to this GPU box."
        )
    train_dataset = load_dataset("json", data_files=str(train_path), split="train")
    eval_dataset = load_dataset("json", data_files=str(dev_path), split="train")

    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if not cfg.get("enable_thinking", True):
        _patch_tokenizer_for_non_thinking(tokenizer)

    quantization_config = BitsAndBytesConfig(
        load_in_4bit=qcfg["load_in_4bit"],
        bnb_4bit_quant_type=qcfg["bnb_4bit_quant_type"],
        bnb_4bit_compute_dtype=getattr(torch, qcfg["bnb_4bit_compute_dtype"]),
        bnb_4bit_use_double_quant=qcfg["bnb_4bit_use_double_quant"],
    )
    peft_config = LoraConfig(
        r=lcfg["r"],
        lora_alpha=lcfg["alpha"],
        lora_dropout=lcfg["dropout"],
        target_modules=lcfg["target_modules"],
        task_type="CAUSAL_LM",
    )

    output_dir = REPO_ROOT / tcfg["output_dir"]
    sft_config = SFTConfig(
        output_dir=str(output_dir),
        max_length=tcfg["max_seq_length"],
        num_train_epochs=tcfg["num_train_epochs"],
        per_device_train_batch_size=tcfg["per_device_train_batch_size"],
        gradient_accumulation_steps=tcfg["gradient_accumulation_steps"],
        learning_rate=tcfg["learning_rate"],
        lr_scheduler_type=tcfg["lr_scheduler_type"],
        logging_steps=tcfg["logging_steps"],
        save_steps=tcfg["save_steps"],
        eval_steps=tcfg["eval_steps"],
        eval_strategy="steps",
        # No trainer-level AMP (fp16/bf16 both off): the quantized base
        # layers already compute in bnb_4bit_compute_dtype (float16,
        # above) and the small set of trainable LoRA params run fine in
        # float32. Layering AMP's GradScaler on top of 4-bit quantization
        # is what caused a chain of bf16/fp16 mismatch errors on T4 — this
        # is the standard QLoRA pattern, not a T4-specific workaround.
        fp16=False,
        bf16=False,
        model_init_kwargs={"torch_dtype": torch.float16},
        seed=cfg["seed"],
        # Loss only on assistant turns, not the system/user prompt — uses
        # a fused kernel that requires an Ampere+ GPU (see
        # configs/finetune.yaml's comment); on T4 this is false and loss
        # computes over the full sequence instead.
        assistant_only_loss=tcfg["assistant_only_loss"],
        report_to=["mlflow"],
    )
    # warmup_ratio/weight_decay are standard TrainingArguments fields but
    # this TRL version's SFTConfig.__init__ rejects them as constructor
    # kwargs; they're still real dataclass fields, so set them directly.
    sft_config.warmup_ratio = tcfg["warmup_ratio"]
    sft_config.weight_decay = tcfg["weight_decay"]

    with mlflow.start_run(run_name=f"qlora-{base_model.split('/')[-1]}"):
        mlflow.log_params(
            {
                "base_model": base_model,
                "seed": cfg["seed"],
                **{f"quant_{k}": v for k, v in qcfg.items()},
                **{f"lora_{k}": v for k, v in lcfg.items()},
                **{f"train_{k}": v for k, v in tcfg.items()},
            }
        )
        trainer = SFTTrainer(
            model=base_model,
            args=sft_config,
            train_dataset=train_dataset,
            eval_dataset=eval_dataset,
            processing_class=tokenizer,
            quantization_config=quantization_config,
            peft_config=peft_config,
        )
        trainer.train()
        trainer.save_model(str(output_dir))

    print(f"LoRA adapter saved to {output_dir}")
    print("Next: python src/noc_copilot/finetune/merge_adapter.py")


if __name__ == "__main__":
    main()
