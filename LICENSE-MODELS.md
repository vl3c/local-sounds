# Model licences

This repo's own code is MIT (see `LICENSE`). It does **not** include or redistribute any
model weights or third-party code: `install.ps1` fetches them from their upstream sources,
and they stay under their own licences. `install.ps1 -AcceptModelLicenses` (or
`download_models.py --accept-licenses`) says you've read them.

## What this means for you

This is a plain-language summary, not legal advice; the licence texts decide. Stable Audio 3's
is in `models/sa3-small-sfx/LICENSE.md` after install, and at <https://stability.ai/license>.

- **Sounds made with Stable Audio 3** (the engine), under the Stability AI Community
  License:
  - **Personal, hobby, research and other non-commercial use is free.**
  - **Commercial use is free too, while you and your affiliates together make less than
    US $1,000,000 a year in total revenue** (from anything, not only from this), but you must
    **register with Stability AI first**: <https://stability.ai/community-license>. Above that
    threshold you need an enterprise licence from Stability AI.
  - **You own the sounds you generate** (as far as the law allows). The licence says outputs
    are not "derivative works" of the model, so a film, game or video using them doesn't
    have to carry the licence.
  - The sounds, like the model, must follow Stability AI's
    [Acceptable Use Policy](https://stability.ai/use-policy), and must not be used to train or
    improve another foundational generative AI model.
  - If you **distribute the model itself** or a product or service built on it, the licence
    asks you to pass on a copy of it, keep its notice, and show "Powered by Stability AI".
    This repo is such a tool: **Powered by Stability AI.**
- **The scorers** (CLAP, Audiobox Aesthetics) and the optional captioner only judge takes;
  nothing they produce ends up in your sounds.

## Components

| Component | Source | Licence | What it means here |
|---|---|---|---|
| Stable Audio 3 Small-SFX weights | [stabilityai/stable-audio-3-small-sfx](https://huggingface.co/stabilityai/stable-audio-3-small-sfx) (gated) | [Stability AI Community License](https://stability.ai/license) (copy in `models/sa3-small-sfx/LICENSE.md` after install) | See above. Not OSI open source. You accept it on Hugging Face before downloading. |
| T5Gemma text encoder (bundled with SA3) | Google | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) (`models/sa3-small-sfx/LICENSE_GEMMA.md`) | Covers the encoder inside the SA3 download, including Gemma's prohibited-use policy. |
| stable-audio-3 inference code | [Stability-AI/stable-audio-3](https://github.com/Stability-AI/stable-audio-3) (cloned into `engine/`) | MIT | |
| CLAP (larger_clap_general) | [laion/larger_clap_general](https://huggingface.co/laion/larger_clap_general) | Apache-2.0 | Used only to score takes against their prompt. |
| Audiobox Aesthetics | [facebook/audiobox-aesthetics](https://huggingface.co/facebook/audiobox-aesthetics), [code](https://github.com/facebookresearch/audiobox-aesthetics) | CC-BY-4.0 (WavLM parts MIT) | Used only to score production quality. Credit: Meta AI, Audiobox Aesthetics. |
| MiDashengLM 0.6B (optional) | [mispeech/midashenglm-0.6b-fp32](https://huggingface.co/mispeech/midashenglm-0.6b-fp32) | Apache-2.0 | The optional captioner. |
| Pedalboard | [spotify/pedalboard](https://github.com/spotify/pedalboard) | GPL-3.0 | Installed into the venv as a library; not redistributed. |

Every library above is installed from upstream by `install.ps1`, not shipped here. If you
redistribute a bundle that includes Pedalboard (a packaged build, an installer with the venv
inside), the GPL-3.0 applies to that bundle.

Not used, on purpose: MOSS-SoundEffect v2.0 (Apache-2.0; the second engine until October 2026,
removed after losing the blind bake-off 9-4 and being ~40x slower), HunyuanVideo-Foley (its
licence excludes the EU), Foley-Omni (pinned to CUDA 12.4 and FlashAttention 2.7), Sony Woosh
(CC-BY-NC weights; the fallback if the bake-off had disappointed), Stable Audio 3 Medium (needs
FlashAttention 2).

The file-name categories follow the [Universal Category System](https://universalcategorysystem.com)
(UCS v8.2), a free public standard.
