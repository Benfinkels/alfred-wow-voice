#!/usr/bin/env python3
"""Unsloth QLoRA Fine-Tune & Q4_K_M GGUF Export for alfred-v2 (Stock Gemma 4 E4B IT).

Trains on native Gemma 4 `<|turn>...<turn|>` chat format (`enable_thinking=False`) with
assistant-response-only loss masking, runs held-out 48-turn verification, and exports
a text-ready Q4_K_M GGUF (~5.3 GB, 100% GPU on an 8 GB RTX 2080).

Usage (Unsloth Python env on CUDA GPU):
  python3 training/train_alfred_v2_qlora.py \
      --train-file ./data/alfred_v2_train.jsonl \
      --eval-file  ./data/alfred_v2_eval.jsonl \
      --out-dir    ./alfred_v2_build
"""
import argparse, json, os, re, time

os.environ["UNSLOTH_ENV_DROP_2"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import unsloth  # noqa: F401,E402 — must precede trl/transformers for fast Triton kernels
from unsloth import FastLanguageModel  # noqa: E402
from unsloth.chat_templates import train_on_responses_only  # noqa: E402
import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402


def get_text_tokenizer(tok):
    return getattr(tok, "tokenizer", tok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-model", default="unsloth/gemma-4-E4B-it-unsloth-bnb-4bit")
    ap.add_argument("--train-file", required=True)
    ap.add_argument("--eval-file", required=True)
    ap.add_argument("--out-dir", default=os.path.expanduser("~/vault/alfred_v2_build"))
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--skip-gguf", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    os.makedirs(args.out_dir, exist_ok=True)
    adapter_dir = os.path.join(args.out_dir, "lora_adapter")
    gguf_dir = os.path.join(args.out_dir, "gguf")

    print(f"[{time.strftime('%H:%M:%S')}] Loading {args.base_model} on {torch.cuda.get_device_name(0)}...")
    model, proc_or_tok = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=768,
        load_in_4bit=True,
    )
    tok = get_text_tokenizer(proc_or_tok)

    model = FastLanguageModel.get_peft_model(
        model,
        finetune_vision_layers=False,
        finetune_language_layers=True,
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
        r=32,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_alpha=64,
        lora_dropout=0.0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )

    train_rows = [json.loads(l) for l in open(args.train_file, encoding="utf-8") if l.strip()]
    eval_rows = [json.loads(l) for l in open(args.eval_file, encoding="utf-8") if l.strip()]

    def to_text(row):
        rendered = proc_or_tok.apply_chat_template(
            row["messages"], tokenize=False, add_generation_prompt=False, enable_thinking=False
        )
        # Strip duplicate leading <bos> because tokenizer adds <bos> during tokenization
        if rendered.startswith("<bos>"):
            rendered = rendered[len("<bos>"):]
        return {"text": rendered}

    train_ds = Dataset.from_list([to_text(r) for r in train_rows])
    print(f"[{time.strftime('%H:%M:%S')}] Sample formatted training turn:\n{train_ds[0]['text'][:420]}\n---")

    sft_cfg = SFTConfig(
        output_dir=os.path.join(args.out_dir, "checkpoints"),
        dataset_text_field="text",
        max_seq_length=768,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,
        warmup_steps=15,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        bf16=True,
        fp16=False,
        logging_steps=15,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=3407,
        save_strategy="no",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        processing_class=tok,
        train_dataset=train_ds,
        args=sft_cfg,
    )
    trainer = train_on_responses_only(
        trainer,
        instruction_part="<|turn>user\n",
        response_part="<|turn>model\n",
    )

    print(f"[{time.strftime('%H:%M:%S')}] Starting QLoRA training ({len(train_ds)} turns, {args.epochs} epochs)...")
    stats = trainer.train()
    print(f"[{time.strftime('%H:%M:%S')}] Training finished in {time.time() - t0:.1f}s (final loss={stats.training_loss:.4f})")

    model.save_pretrained(adapter_dir)
    proc_or_tok.save_pretrained(adapter_dir)

    # Run fast held-out 48-turn behavioral evaluation
    print(f"[{time.strftime('%H:%M:%S')}] Evaluating {len(eval_rows)} held-out turns...")
    FastLanguageModel.for_inference(model)
    scores = {"wow_lookup": [0, 0], "wow_escalate": [0, 0], "banter": [0, 0], "ticket": [0, 0]}
    turn_end_id = tok.convert_tokens_to_ids("<turn|>")
    eos_ids = [i for i in {tok.eos_token_id, turn_end_id} if i is not None and i >= 0]

    for r in eval_rows:
        cat = r["category"]
        scores[cat][1] += 1
        prompt = proc_or_tok.apply_chat_template(
            r["messages"][:2], tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        if prompt.startswith("<bos>"):
            prompt = prompt[len("<bos>"):]
        inputs = tok(prompt, return_tensors="pt").to("cuda")
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=80,
                do_sample=False,
                eos_token_id=eos_ids,
                pad_token_id=tok.eos_token_id,
            )
        gen = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        gen = re.sub(r"^<\|thought>\s*<thought\|>\s*", "", gen).strip()
        ok = False
        if cat == "wow_escalate":
            ok = gen.upper() == "ESCALATE"
        elif cat == "wow_lookup":
            gold_coords = set(re.findall(r"(\d{1,2}\.\d),\s*(\d{1,2}\.\d)", r["messages"][2]["content"]))
            pred_coords = set(re.findall(r"(\d{1,2}\.\d),\s*(\d{1,2}\.\d)", gen))
            ok = bool(pred_coords) and pred_coords.issubset(gold_coords) and "#" not in gen and "ESCALATE" not in gen.upper()
        elif cat == "banter":
            ok = 3 <= len(gen.split()) <= 40 and not re.search(r"\d{1,2}\.\d,\s*\d{1,2}\.\d", gen) and not re.search(r"\b(imbecile|whelp|certainly,\s*master)\b", gen, re.I)
        elif cat == "ticket":
            ok = 3 <= len(gen.split()) <= 16 and '"' not in gen
        if ok:
            scores[cat][0] += 1
        else:
            print(f"  [MISS:{cat}] pred={gen!r} | gold={r['messages'][2]['content']!r}")

    eval_summary = {k: f"{v[0]}/{v[1]} ({100.0 * v[0] / max(1, v[1]):.1f}%)" for k, v in scores.items()}
    print(f"[{time.strftime('%H:%M:%S')}] Held-out Eval Summary: {json.dumps(eval_summary)}")
    with open(os.path.join(args.out_dir, "eval_results.json"), "w", encoding="utf-8") as ef:
        json.dump({"training_loss": stats.training_loss, "scores": scores, "summary": eval_summary}, ef, indent=2)

    if not args.skip_gguf:
        import gc, subprocess, sys
        del trainer
        gc.collect()
        torch.cuda.empty_cache()
        t_exp = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] Exporting merged Q4_K_M GGUF to {gguf_dir}...")
        os.makedirs(gguf_dir, exist_ok=True)
        try:
            model.save_pretrained_gguf(gguf_dir, proc_or_tok, quantization_method="q4_k_m")
        except RuntimeError as e:
            # Fallback if Unsloth's online convert_hf_to_gguf fetch fails after writing merged safetensors
            llama_dir = os.path.expanduser("~/.unsloth/llama.cpp")
            conv_py = os.path.join(llama_dir, "unsloth_convert_hf_to_gguf.py")
            quant_bin = os.path.join(llama_dir, "llama-quantize")
            if not (os.path.exists(os.path.join(gguf_dir, "model.safetensors")) and os.path.exists(conv_py) and os.path.exists(quant_bin)):
                raise e
            bf16_path = os.path.join(args.out_dir, "alfred-v2-bf16.gguf")
            q4_path = os.path.join(args.out_dir, "alfred-v2-q4_k_m.gguf")
            subprocess.check_call([sys.executable, conv_py, gguf_dir, "--outfile", bf16_path, "--outtype", "bf16"])
            subprocess.check_call([quant_bin, bf16_path, q4_path, "Q4_K_M"])
            os.remove(bf16_path)
        print(f"[{time.strftime('%H:%M:%S')}] GGUF export finished in {time.time() - t_exp:.1f}s!")


if __name__ == "__main__":
    main()
