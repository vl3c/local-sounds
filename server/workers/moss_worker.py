"""MOSS-SoundEffect v2.0 worker (runs in .venv-moss). See protocol.py for the protocol.

48 kHz mono, up to 30 s, 100 flow-matching steps by default, CFG 4 with a negative prompt.
The pipeline denoises a full 30 s latent and crops it (that's how it was trained); `window`
shrinks that for short sounds, but it saves little time (~20%) and in tests gave quieter or
cut-off takes, so the default stays at 30 s. Seeds are per take: each batch item gets its own noise from its own
seed, so a take can be reproduced alone.

torch.compile is switched off (TORCHDYNAMO_DISABLE=1): Triton isn't reliable on Windows,
and the upstream README recommends the same.
"""

from __future__ import annotations

import os

os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

import protocol  # noqa: E402,F401  (takes stdout for the protocol)

import math  # noqa: E402
from pathlib import Path  # noqa: E402

from protocol import MODELS, log, serve  # noqa: E402

NAME = "moss"
MODEL_DIR = MODELS / "moss-sfx-v2"
MAX_DURATION = 30.0
MAX_BATCH = 1  # one take at a time: each is a 30 s latent (x2 for CFG); batches overflowed 16 GB


def load():
    import torch
    from moss_soundeffect_v2 import MossSoundEffectPipeline

    pipe = MossSoundEffectPipeline.from_pretrained(str(MODEL_DIR), torch_dtype=torch.bfloat16, device="cuda")
    engine = pipe.engine
    # The 1.3B DiT loads in fp32 (5.3 GB) although it runs under bf16 autocast, where its
    # matmuls are bf16 anyway. bf16 weights (parameters only: its RoPE buffer is complex)
    # measured 15.7 s instead of 27.8 s a take and a 9.6 GB peak instead of 14.9 GB, which
    # is what lets it share the card with Stable Audio 3 and the checkers.
    for p in engine.dit.parameters():
        p.data = p.data.to(torch.bfloat16)
    torch.cuda.empty_cache()
    orig = engine.generate_noise

    def per_seed_noise(shape, seed=None, rand_device="cpu", **kw):
        seeds = getattr(engine, "_seeds", None)
        if not seeds or len(seeds) != shape[0]:
            return orig(shape, seed=seed, rand_device=rand_device, **kw)
        return torch.cat([orig((1, *shape[1:]), seed=s, rand_device=rand_device, **kw) for s in seeds])

    engine.generate_noise = per_seed_noise
    # Warm up kernels so the first real request isn't the slow one.
    pipe("a short click", seconds=1.0, num_inference_steps=2, max_inference_seconds=10, progress_bar_cmd=lambda x: x)
    torch.cuda.empty_cache()
    return pipe, {"engine": NAME, "model": "moss-soundeffect-v2.0", "sample_rate": pipe.sample_rate,
                  "channels": 1, "max_duration": MAX_DURATION, "default_steps": 100,
                  "features": ["negative", "cfg", "seed", "window"], "ignores": []}


def handle(pipe, req: dict) -> dict:
    import soundfile as sf
    import torch

    duration = min(float(req["duration"]), MAX_DURATION)
    window = req.get("window") or MAX_DURATION
    window = int(min(MAX_DURATION, max(window, math.ceil(duration))))
    out_dir = Path(req["out_dir"])
    seeds = [int(s) for s in req["seeds"]]
    files = []
    for i in range(0, len(seeds), MAX_BATCH):
        batch = seeds[i:i + MAX_BATCH]
        pipe.engine._seeds = [s % 2**31 for s in batch]
        try:
            audio = pipe([req["prompt"]] * len(batch) if len(batch) > 1 else req["prompt"], seconds=duration,
                         num_inference_steps=int(req.get("steps") or 100), cfg_scale=float(req.get("cfg") or 4.0),
                         seed=batch[0] % 2**31, negative_prompt=req.get("negative") or "",
                         max_inference_seconds=window, progress_bar_cmd=lambda x: x)
        finally:
            pipe.engine._seeds = None
        for k, seed in enumerate(batch):
            path = out_dir / f"{req['stem']}_{seed}.wav"
            sf.write(str(path), audio[k].float().cpu().numpy().T, pipe.sample_rate, subtype="FLOAT")
            files.append({"seed": seed, "path": str(path)})
    torch.cuda.empty_cache()
    log(NAME, f"{len(files)} take(s), {duration:.1f}s each (window {window}s): {req['prompt'][:70]}")
    return {"files": files, "sample_rate": pipe.sample_rate, "window": window}


if __name__ == "__main__":
    serve(NAME, load, handle)
