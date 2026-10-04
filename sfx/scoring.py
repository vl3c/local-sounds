"""Model-based scores, run in the server process (main venv, on the GPU).

- CLAP (laion/larger_clap_general): how well a take matches its prompt, as a softmax of the
  prompt against distractor captions (music, speech, silence, near misses). A *relative*
  score, for ranking takes of the same request: never an absolute pass/fail.
- Audiobox Aesthetics (facebook/audiobox-aesthetics): production quality (PQ), production
  complexity (PC), content enjoyment (CE) and usefulness (CU), each ~1-10. PQ is the noise /
  artefact gate.
- MiDashengLM 0.6B (optional): an audio captioner that says what a take actually contains.
  Loaded only when asked and freed straight afterwards.

Every class loads from models/<name> with the Hub offline.
"""

from __future__ import annotations

import gc
import time
from pathlib import Path

import numpy as np

from .audio import as2d, resample

# PQ below this is rejected as noisy / broken. Calibrated in the bake-off (see README).
PQ_FAIL = 4.0
PQ_WARN = 5.5
CLAP_SCALE = 20.0


def _free() -> None:
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


class Clap:
    SR = 48000

    def __init__(self, model_dir: Path):
        import torch
        from transformers import ClapModel, ClapProcessor
        t0 = time.time()
        self.torch = torch
        self.model = ClapModel.from_pretrained(str(model_dir)).to("cuda").eval()
        self.proc = ClapProcessor.from_pretrained(str(model_dir))
        print(f"[local-sounds] CLAP loaded in {time.time() - t0:.1f}s", flush=True)

    def score(self, audios: list[np.ndarray], sr: int, prompt: str, distractors: list[str]) -> list[dict]:
        """For each take: {"clap": P(prompt | audio) among prompt + distractors, "clap_cos": raw cosine,
        "clap_top": the best-matching caption when it isn't the prompt}."""
        torch = self.torch
        mono = [resample(as2d(a), sr, self.SR).mean(axis=0) for a in audios]
        texts = [prompt] + list(distractors)
        with torch.inference_mode():
            ti = self.proc(text=texts, return_tensors="pt", padding=True).to("cuda")
            t = self.model.get_text_features(**ti)
            ai = self.proc(audios=mono, sampling_rate=self.SR, return_tensors="pt").to("cuda")
            a = self.model.get_audio_features(**ai)
            t = t / t.norm(dim=-1, keepdim=True)
            a = a / a.norm(dim=-1, keepdim=True)
            cos = (a @ t.T).float()
            # CLAP's own logit scale (~100) saturates the softmax at 0.99+ for anything vaguely
            # right; a softer temperature keeps the numbers apart so takes can be ranked.
            probs = (cos * CLAP_SCALE).softmax(dim=-1).cpu().numpy()
            cos = cos.cpu().numpy()
        out = []
        for p, c in zip(probs, cos):
            top = int(np.argmax(p))
            out.append({"clap": round(float(p[0]), 4), "clap_cos": round(float(c[0]), 4),
                        "clap_top": None if top == 0 else texts[top]})
        return out


class Aesthetics:
    def __init__(self, model_dir: Path):
        import torch
        from audiobox_aesthetics.infer import AXES_NAME, AesPredictor, Normalize
        from audiobox_aesthetics.model.aes import AesMultiOutput
        t0 = time.time()
        pred = AesPredictor.__new__(AesPredictor)  # skip its __post_init__, which loads from the Hub
        pred.checkpoint_pth, pred.precision, pred.batch_size = None, "bf16", 1
        pred.data_col, pred.sample_rate, pred.device = "path", 16000, torch.device("cuda")
        model = AesMultiOutput.from_pretrained(str(model_dir)).to(pred.device).eval()
        pred.model = model
        pred.target_transform = {ax: Normalize(mean=model.target_transform[ax]["mean"],
                                               std=model.target_transform[ax]["std"]) for ax in AXES_NAME}
        self.pred, self.torch = pred, torch
        print(f"[local-sounds] Audiobox Aesthetics loaded in {time.time() - t0:.1f}s", flush=True)

    def score(self, audios: list[np.ndarray], sr: int) -> list[dict]:
        torch = self.torch
        batch = [{"path": torch.from_numpy(as2d(a).copy()), "sample_rate": sr} for a in audios]
        rows = self.pred.forward(batch)
        return [{k: round(float(v), 2) for k, v in r.items()} for r in rows]


class Captioner:
    """MiDashengLM 0.6B. Use as a context manager: it is freed on exit."""

    SR = 16000

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir

    def __enter__(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor, AutoTokenizer
        t0 = time.time()
        self.torch = torch
        self.model = AutoModelForCausalLM.from_pretrained(str(self.model_dir), trust_remote_code=True,
                                                          dtype=torch.bfloat16).to("cuda").eval()
        self.tok = AutoTokenizer.from_pretrained(str(self.model_dir))
        self.proc = AutoProcessor.from_pretrained(str(self.model_dir), trust_remote_code=True)
        print(f"[local-sounds] captioner loaded in {time.time() - t0:.1f}s", flush=True)
        return self

    def caption(self, audio: np.ndarray, sr: int) -> str:
        torch = self.torch
        mono = resample(as2d(audio), sr, self.SR).mean(axis=0)
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Describe this sound effect in one short sentence."},
            {"type": "audio", "audio": mono}]}]
        with torch.inference_mode():
            inputs = self.proc.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                   add_special_tokens=True, return_dict=True)
            inputs = inputs.to(device=self.model.device, dtype=self.model.dtype)
            out = self.model.generate(**inputs, max_new_tokens=60, do_sample=False)
        text = self.tok.batch_decode(out, skip_special_tokens=True)[0]
        return text.strip().splitlines()[-1].strip() if text.strip() else ""

    def __exit__(self, *exc):
        del self.model
        _free()


def rank_score(take: dict) -> float:
    """One number to sort takes of the same request by. Higher is better.

    The prompt match counts twice: CLAP's cosine similarity (~0.1 poor, 0.25 fair, 0.35+ good;
    0.45 earns full marks)
    and its softmax against the distractors (relative). Audiobox PQ carries the quality
    (scaled 3..8 -> 0..1). Each warning costs a little; failed takes sort last regardless.
    """
    if take.get("fails"):
        return -1.0
    cos = min(1.0, max(0.0, take.get("clap_cos", 0.0) / 0.45))
    soft = take.get("clap", 0.0)
    pq = take.get("aes", {}).get("PQ")
    q = 0.5 if pq is None else min(1.0, max(0.0, (pq - 3.0) / 5.0))
    return round(0.4 * cos + 0.2 * soft + 0.4 * q - 0.05 * len(take.get("warnings", [])), 4)
