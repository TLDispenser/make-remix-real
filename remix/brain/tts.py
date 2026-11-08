# tts.py — Remix's voice. espeak-ng, deliberately NOT an AI voice (user rule).
#
# Per protocol step 4: synthesize each beat, join them into ONE PCM WAV with a
# short silence between beats, and report where each beat starts. The brain
# turns those into `perform` cue times, so Godot fires every face change from
# the audio's own clock and the mouth follows the live audio level.
#
# espeak-ng writes a WAV to stdout (--stdout) from text on stdin (--stdin): no
# per-beat temp files, no shell quoting. Only the joined file touches disk, in a
# tmpfs-ish runtime dir, and old ones are pruned. (The mic's audio is the thing
# that must never touch disk; this is her own voice.)
import asyncio
import io
import re
import shutil
import time
import wave
from pathlib import Path
from typing import NamedTuple

from . import config

# Read letter-by-letter or fixed: espeak guesses these badly.
_ACRONYMS = {"cpu": "C P U", "gpu": "G P U", "ssd": "S S D", "usb": "U S B",
             "pc": "P C", "os": "O S", "ui": "U I", "ai": "A I", "wifi": "wi fi",
             "hdd": "H D D", "bios": "bye ose", "linux": "linnux"}
_ACR_RE = re.compile(r"\b(" + "|".join(_ACRONYMS) + r")\b", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")


class Speech(NamedTuple):
    path: str
    starts: list      # seconds into the file where each beat begins
    durations: list   # each beat's own length (seconds)
    total: float


def speakable(text: str) -> str:
    """Clean a beat for espeak: no markup, emoji, or symbols it would read aloud."""
    t = _EMOJI.sub("", text)
    t = re.sub(r"[*_`#>~|\\]", "", t)
    t = re.sub(r"\((.*?)\)", r", \1,", t)                       # parentheses -> pauses
    t = re.sub(r"(\d+(?:\.\d+)?)\s*°?\s*C\b", r"\1 degrees", t)  # 53C -> 53 degrees
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = _ACR_RE.sub(lambda m: _ACRONYMS[m.group(1).lower()], t)
    t = re.sub(r"\.{2,}|…", ", ", t)                              # sigh = short pause
    t = re.sub(r"([.,!?])\s*,", r"\1", t)                         # no ". ," leftovers
    t = re.sub(r"\s+", " ", t).strip(" ,")
    return t


def available() -> bool:
    return config.TTS_ENABLED and shutil.which(config.TTS_BIN) is not None


async def _synth_one(text: str) -> bytes | None:
    """One beat -> WAV bytes (22.05 kHz mono s16), or None."""
    spoken = speakable(text)
    if not spoken:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            config.TTS_BIN, "--stdin", "--stdout",
            "-v", config.TTS_VOICE, "-s", str(config.TTS_SPEED),
            "-p", str(config.TTS_PITCH), "-a", str(config.TTS_AMP),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(spoken.encode()), 15.0)
    except (OSError, asyncio.TimeoutError) as e:
        print(f"remix-brain: tts failed ({e!r})")
        return None
    return out if proc.returncode == 0 and len(out) > 44 else None


def _frames(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return (w.getnchannels(), w.getsampwidth(), w.getframerate(),
                w.readframes(w.getnframes()))


async def synthesize(beats: list, tag: str) -> Speech | None:
    """Beats -> one WAV + beat start times. None = no voice (caller shows captions)."""
    if not available() or not beats:
        return None
    pieces = []
    for b in beats:
        wav = await _synth_one(b)
        pieces.append(_frames(wav) if wav else None)
    fmt = next((p[:3] for p in pieces if p), None)
    if fmt is None:
        return None
    ch, width, rate = fmt
    gap = b"\x00" * (int(rate * config.TTS_BEAT_GAP_S) * ch * width)
    body = bytearray()
    starts, durs = [], []
    for p in pieces:
        starts.append(len(body) / (rate * ch * width))
        if p and p[:3] == fmt:
            body.extend(p[3])
            durs.append(len(p[3]) / (rate * ch * width))
        else:
            durs.append(0.0)
        body.extend(gap)
    d = Path(config.TTS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    _prune(d)
    path = d / f"{tag}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(body))
    d_total = len(body) / (rate * ch * width)
    return Speech(str(path), starts, durs, d_total)


def _prune(d: Path, keep: int = 6) -> None:
    files = sorted(d.glob("*.wav"), key=lambda f: f.stat().st_mtime)
    for f in files[:-keep] if len(files) > keep else []:
        try:
            f.unlink()
        except OSError:
            pass
