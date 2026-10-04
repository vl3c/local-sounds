# Model licences

This repo's own code is MIT (see `LICENSE`). It does **not** include or redistribute any
model weights or third-party code: `install.ps1` fetches them from their upstream sources,
and they stay under their own licences. Check them yourself before any commercial use.

| Component | Source | Licence | What it means here |
|---|---|---|---|
| Stable Audio 3 Small-SFX weights | [stabilityai/stable-audio-3-small-sfx](https://huggingface.co/stabilityai/stable-audio-3-small-sfx) (gated) | [Stability AI Community License](https://stability.ai/license) (copy in `models/sa3-small-sfx/LICENSE.md` after install) | Free for non-commercial use, and for commercial use under the revenue threshold the licence sets; not OSI open source. You accept it on Hugging Face before downloading. |
| T5Gemma text encoder (bundled with SA3) | Google | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) (`models/sa3-small-sfx/LICENSE_GEMMA.md`) | Covers the encoder inside the SA3 download. |
| stable-audio-3 inference code | [Stability-AI/stable-audio-3](https://github.com/Stability-AI/stable-audio-3) (cloned into `engine/`) | MIT | |
| MOSS-SoundEffect v2.0 weights | [OpenMOSS-Team/MOSS-SoundEffect-v2.0](https://huggingface.co/OpenMOSS-Team/MOSS-SoundEffect-v2.0) | Apache-2.0 | Genuinely open source; its text encoder is Qwen3-1.7B (Apache-2.0). |
| MOSS-SoundEffect v2 code | [OpenMOSS/MOSS-TTS](https://github.com/OpenMOSS/MOSS-TTS/tree/main/moss_soundeffect_v2) (cloned into `engine/`) | Apache-2.0 | |
| CLAP (larger_clap_general) | [laion/larger_clap_general](https://huggingface.co/laion/larger_clap_general) | Apache-2.0 | Used only to score takes against their prompt. |
| Audiobox Aesthetics | [facebook/audiobox-aesthetics](https://huggingface.co/facebook/audiobox-aesthetics), [code](https://github.com/facebookresearch/audiobox-aesthetics) | CC-BY-4.0 (WavLM parts MIT) | Used only to score production quality. |
| MiDashengLM 0.6B (optional) | [mispeech/midashenglm-0.6b-fp32](https://huggingface.co/mispeech/midashenglm-0.6b-fp32) | Apache-2.0 | The optional captioner. |
| Pedalboard | [spotify/pedalboard](https://github.com/spotify/pedalboard) | GPL-3.0 | Installed into the venv as a library; not redistributed. |

Not used, on purpose: HunyuanVideo-Foley (its licence excludes the EU), Foley-Omni (pinned to
CUDA 12.4 and FlashAttention 2.7), Sony Woosh (CC-BY-NC weights; the fallback if both engines
had disappointed), Stable Audio 3 Medium (needs FlashAttention 2).

The file-name categories follow the [Universal Category System](https://universalcategorysystem.com)
(UCS v8.2), a free public standard.
