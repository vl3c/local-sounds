"""Prompt recipes, CLAP distractor captions and UCS-style file names.

Text-to-audio models do best with one sound event described the way a sound library would
caption it: the source and material, the action, the envelope (attack and decay), and the
recording ("close-mic, dry, no reverb"). `build_prompt` turns a terse request ("sword hit")
into that shape using the matching category's recipe; pass raw=True to send text untouched.

Categories carry the Universal Category System (UCS v8.2) CatID used in file names, e.g.
METLImpt_SwordHit_v01.wav. UCS has no IMPACTS category: impacts file under their material
(METLImpt, WOODImpt, GLASImpt, ROCKImpt...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_NEGATIVE = "low quality, distorted, music, speech, voice, reverb, background noise"
DRY = "close-mic, dry, no reverb"


@dataclass(frozen=True)
class Category:
    key: str
    cat_id: str                  # UCS CatID
    mode: str                    # oneshot | ambience | loop
    duration: float              # a sensible default length, seconds
    keywords: tuple[str, ...]    # matched against the request to pick the category
    envelope: str                # added to the prompt: attack / decay / character
    near_misses: tuple[str, ...]  # CLAP distractors: plausible confusions
    example: str = ""            # a terse request and the prompt it becomes (docs)
    dry: bool = True             # add "close-mic, dry, no reverb"
    negative_extra: str = ""
    tags: tuple[str, ...] = field(default=())


CATEGORIES: list[Category] = [
    Category("impact-metal", "METLImpt", "oneshot", 1.5,
             ("metal", "sword", "anvil", "clang", "steel", "iron", "pipe", "shield", "pan", "bell hit"),
             "single sharp metallic impact, bright ringing tail, short decay",
             ("a wooden knock", "glass shattering", "a dull thud on cardboard")),
    Category("impact-wood", "WOODImpt", "oneshot", 1.0,
             ("wood", "wooden", "plank", "door", "slam", "knock", "crate", "log", "stick", "drawer"),
             "single solid wooden knock, sharp attack, short dry decay",
             ("a metal clang", "a rock hitting concrete", "a cardboard box thump")),
    Category("impact-glass", "GLASImpt", "oneshot", 1.5,
             ("glass", "bottle", "window", "shatter", "wine glass"),
             "single glass impact, sharp attack, bright shards, short decay",
             ("a metal clang", "ice cubes in a glass", "ceramic plate breaking")),
    Category("impact-stone", "ROCKImpt", "oneshot", 1.2,
             ("rock", "stone", "boulder", "concrete", "brick", "pebble"),
             "single heavy stone impact, sharp attack, gritty short decay",
             ("a wooden knock", "a metal clang", "dirt falling")),
    Category("impact-body", "FGHTImpt", "oneshot", 0.8,
             ("punch", "kick", "slap", "body hit", "hit", "smack", "whack", "body fall", "bodyfall"),
             "single punchy body impact, fleshy thump, sharp attack, very short decay",
             ("a wooden knock", "a metal clang", "a hand clap")),
    Category("whoosh", "WHSH", "oneshot", 1.2,
             ("whoosh", "swoosh", "swish", "swipe", "swing", "fly by", "flyby", "pass by", "woosh"),
             "single fast air whoosh passing by, swells in and fades out quickly",
             ("wind blowing steadily", "a gunshot", "a car passing by"),
             negative_extra="impact"),
    Category("ui", "UIClick", "oneshot", 0.5,
             ("ui", "click", "blip", "beep", "button", "notification", "menu", "interface", "chime", "select",
              "confirm", "error sound", "tick"),
             "single short clean electronic interface sound, instant attack, very short decay",
             ("a mechanical keyboard typing", "a phone ringing", "a bird chirp"),
             dry=False),
    Category("footsteps", "FEETHmn", "oneshot", 2.0,
             ("footstep", "footsteps", "steps", "walking", "walk", "running", "run on", "boots", "heels",
              "sneakers"),
             "single footstep, crisp heel and sole contact, short decay",
             ("a door knock", "a hand clap", "rain on a roof")),
    Category("cartoon-boing", "TOONBoing", "oneshot", 1.0,
             ("boing", "spring", "bounce", "bouncy", "jaw harp", "twang"),
             "single cartoon boing, springy wobbling pitch, short decay",
             ("a car horn", "a guitar string pluck", "a dog barking")),
    Category("cartoon-pop", "TOONPop", "oneshot", 0.5,
             ("pop", "bubble pop", "cork", "plop", "bloop"),
             "single cartoon pop, round and bright, instant attack, very short",
             ("a gunshot", "a hand clap", "a balloon slowly deflating")),
    Category("magic", "MAGSpel", "oneshot", 2.5,
             ("magic", "spell", "sparkle", "shimmer", "fairy", "enchant", "potion", "wand", "teleport", "portal",
              "glitter"),
             "single magical spell sound, shimmering sparkles rising, bright airy tail",
             ("wind chimes in the wind", "a glass breaking", "an electric guitar chord"),
             dry=False),
    Category("scifi-machine", "SCIMach", "oneshot", 3.0,
             ("sci-fi", "scifi", "laser", "robot", "spaceship", "space ship", "power up", "power-up", "power down",
              "servo", "forcefield", "force field", "plasma", "hologram", "teleporter", "machine"),
             "single futuristic machine sound, clean electronic texture, clear start and end",
             ("a car engine idling", "a vacuum cleaner", "a synthesizer melody"),
             dry=False),
    Category("creature", "CREAMnstr", "oneshot", 2.0,
             ("creature", "monster", "growl", "roar", "snarl", "hiss", "dragon", "beast", "screech", "goblin",
              "zombie", "alien"),
             "single creature vocalisation, guttural and animal-like, clear start and end",
             ("a person talking", "a lion roaring at a zoo", "a car engine revving"),
             negative_extra="human speech, words"),
    Category("explosion", "EXPLReal", "oneshot", 4.0,
             ("explosion", "explode", "blast", "bomb", "detonation", "boom", "grenade", "kaboom"),
             "single powerful explosion, sudden blast, deep low-end rumble, debris tail",
             ("thunder rolling", "a door slamming", "fireworks crackling")),
    Category("ambience", "AMBMisc", "loop", 10.0,
             ("ambience", "ambient", "ambiance", "room tone", "atmosphere", "background", "forest", "city",
              "rain", "wind", "ocean", "waves", "crowd", "hum", "drone", "night", "cave", "jungle", "traffic",
              "fire crackling", "campfire", "stream", "river", "steady", "continuous"),
             "continuous steady ambience, constant texture, no sudden events",
             ("music playing", "a single loud bang", "people talking nearby"),
             dry=False, negative_extra="sudden loud sounds"),
    Category("misc", "DSGNMisc", "oneshot", 2.0, (), "single clear sound event, clean start and end",
             ("a dog barking", "a car passing by")),
]
BY_KEY = {c.key: c for c in CATEGORIES}

# Always-on CLAP distractors: what a failed generation usually sounds like instead.
GENERIC_DISTRACTORS = ("music with melody and rhythm", "a person speaking", "silence", "white noise",
                       "electronic static and digital glitches")

_AMBIENCE_HINTS = ("ambience", "ambient", "ambiance", "room tone", "atmosphere", "loop", "background")


def detect(text: str) -> Category:
    """The category whose keywords best match the request.

    Keywords match whole words with common endings ("swords", "shattering", "knocked"), and
    a category scores the total length of its matches, so "glass bottle shattering on
    concrete" is glass (glass + bottle + shatter) rather than stone (concrete).
    An explicit ambience word ("ambience", "room tone", "loop"...) always wins.
    """
    t = " " + re.sub(r"[^a-z0-9 -]+", " ", text.lower()) + " "
    best, best_score = BY_KEY["misc"], 0
    for c in CATEGORIES:
        score = 0
        for kw in c.keywords:
            if re.search(rf"(?<![a-z]){re.escape(kw)}(?:s|es|ed|ing|ping|ning)?(?![a-z])", t):
                score += len(kw)
        if score and c.key == "ambience" and any(h in t for h in _AMBIENCE_HINTS):
            score += 100
        if score > best_score:
            best, best_score = c, score
    return best


def _has(text: str, *words: str) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def build_prompt(text: str, category: str | None = None, raw: bool = False, mode: str | None = None) -> dict:
    """{"prompt", "negative", "category", "mode", "duration"} for a request.

    raw=True sends the text as written (still picks a category, for naming and defaults).
    A loop or ambience `mode` gets the ambience recipe unless the category is forced: "rain on
    a window" asked for as a loop is an ambience, not a glass impact.
    """
    text = " ".join(text.split()).strip().rstrip(".")
    cat = BY_KEY[category] if category else detect(text)
    if not category and mode in ("loop", "ambience") and cat.mode == "oneshot":
        cat = BY_KEY["ambience"]
    negative = DEFAULT_NEGATIVE + (f", {cat.negative_extra}" if cat.negative_extra else "")
    if cat.mode != "oneshot":
        negative = negative.replace("reverb, ", "")  # a space is part of an ambience
    out = {"category": cat.key, "cat_id": cat.cat_id, "mode": cat.mode, "duration": cat.duration,
           "negative": negative}
    if raw:
        return {**out, "prompt": text}
    parts = [text]
    if not _has(text, "attack", "decay", "continuous", "steady", "single", "tail", "swell"):
        parts.append(cat.envelope)
    if cat.dry and not _has(text, "reverb", "room", "hall", "dry", "close-mic", "distant", "echo"):
        parts.append(DRY)
    if cat.mode != "oneshot" and not _has(text, "stereo"):
        parts.append("wide stereo field recording")
    return {**out, "prompt": ", ".join(parts)}


def distractors(prompt: str, category: str) -> list[str]:
    """Captions CLAP weighs the prompt against: generic failures plus this category's near misses."""
    cat = BY_KEY.get(category, BY_KEY["misc"])
    out = [d for d in GENERIC_DISTRACTORS + cat.near_misses if d.lower() not in prompt.lower()]
    if cat.mode == "oneshot":
        out.append("a long continuous background ambience")
    else:
        out.append("a single short click")
    return out


# --------------------------------------------------------------------------- names

_STOP = {"a", "an", "the", "of", "on", "in", "into", "with", "and", "to", "at", "by", "for", "from", "sound",
         "sfx", "effect", "single", "very", "some", "short", "long", "close", "mic", "dry", "no", "reverb"}


def fx_name(text: str, max_len: int = 24) -> str:
    """'heavy sword hit on a shield' -> 'HeavySwordHitShield' (UCS FXName: short CamelCase)."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", text.split(",")[0]) if w.lower() not in _STOP]
    name = ""
    for w in words:
        part = w[:1].upper() + w[1:].lower()
        if len(name) + len(part) > max_len:
            break
        name += part
    return name or "Sound"


def ucs_filename(cat_id: str, name: str, version: int | None = 1, ext: str = ".wav") -> str:
    """CatID_FXName_v01.wav (version None: no suffix)."""
    cat_id = re.sub(r"[^A-Za-z0-9]", "", cat_id) or "DSGNMisc"
    name = re.sub(r"[^A-Za-z0-9]", "", name) or "Sound"
    return f"{cat_id}_{name}" + (f"_v{version:02d}" if version is not None else "") + ext
