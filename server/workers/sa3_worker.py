"""Stable Audio 3 Small-SFX worker (runs in .venv-sa3). See protocol.py for the protocol.

The official stable-audio-3 library, loaded from models/sa3-small-sfx instead of the Hub:
its from_pretrained only takes Hub names (upstream issue #35), so this builds the model
the way from_pretrained does, with the text encoder pointed at the local folder.

Notes from upstream: the post-trained small-sfx checkpoint ignores cfg_scale and the
negative prompt (only the -base checkpoints use them); 8 steps is its default. Found here:
lengths are rounded up to whole seconds (see handle()).
"""

from __future__ import annotations

import protocol  # noqa: F401  (first: takes stdout for the protocol)

import json
import math
from pathlib import Path

from protocol import MODELS, log, serve

NAME = "sa3"
MODEL_DIR = MODELS / "sa3-small-sfx"
MAX_DURATION = 120.0


def load():
    import torch
    from stable_audio_3.loading_utils import load_diffusion_cond
    from stable_audio_3.model import StableAudioModel

    cfg = json.loads((MODEL_DIR / "model_config.json").read_text(encoding="utf-8"))
    for c in cfg["model"]["conditioning"]["configs"]:
        if c.get("type") == "t5gemma":
            c["config"].pop("repo_id", None)
            c["config"].pop("subfolder", None)
            c["config"]["model_path"] = str(MODEL_DIR / "t5gemma-b-b-ul2")
    model = load_diffusion_cond(cfg, str(MODEL_DIR / "model.safetensors"), device="cuda", model_half=True)
    model.use_lora, model.lora_names = False, []
    sam = StableAudioModel(model, cfg, "cuda", True)
    # The first generation pays for CUDA kernel selection and allocator growth (~50 s
    # measured); a tiny one here moves that into startup.
    sam.generate(prompt="a short click", duration=1.0, steps=2, seed=0, batch_size=1)
    torch.cuda.empty_cache()
    sr = int(cfg["sample_rate"])
    return sam, {"engine": NAME, "model": "stable-audio-3-small-sfx", "sample_rate": sr,
                 "channels": int(cfg.get("audio_channels", 2)), "max_duration": MAX_DURATION,
                 "default_steps": 8, "features": ["init_audio", "seed"],
                 "ignores": ["negative", "cfg"]}


def handle(sam, req: dict) -> dict:
    import soundfile as sf
    import torch

    sr = int(sam.model_config["sample_rate"])
    # Whole seconds only: some fractional lengths (0.5, 1.25, 2.2, 2.25 s...) make small-sfx
    # put out full-scale clipped static, while every whole second from 1 to 30 tested clean
    # (3 seeds each). Post-processing trims to the length that was asked for anyway.
    duration = float(min(MAX_DURATION, max(1, math.ceil(float(req["duration"]) - 1e-6))))
    init = None
    if req.get("init_audio"):
        a, a_sr = sf.read(req["init_audio"], dtype="float32", always_2d=True)
        init = (a_sr, torch.from_numpy(a.T.copy()))
    out_dir = Path(req["out_dir"])
    files = []
    for seed in req["seeds"]:
        kw = dict(prompt=req["prompt"], negative_prompt=req.get("negative") or None, duration=duration,
                  steps=int(req.get("steps") or 8), cfg_scale=float(req.get("cfg") or 1.0),
                  seed=int(seed) % 2**31, batch_size=1)
        if init is not None:
            kw.update(init_audio=init, init_noise_level=float(req.get("init_strength", 0.5)))
        audio = sam.generate(**kw)  # (1, C, T) float32 in [-1, 1]
        path = out_dir / f"{req['stem']}_{seed}.wav"
        sf.write(str(path), audio[0].float().cpu().numpy().T, sr, subtype="FLOAT")
        files.append({"seed": seed, "path": str(path)})
    torch.cuda.empty_cache()
    log(NAME, f"{len(files)} take(s), {duration:.1f}s each: {req['prompt'][:70]}")
    return {"files": files, "sample_rate": sr}


if __name__ == "__main__":
    serve(NAME, load, handle)
