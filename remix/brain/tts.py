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
import html
import io
import re
import shutil
import time
import wave
import zlib
from pathlib import Path
from typing import NamedTuple

from . import config, vocal_fx

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


_STAGE_DIR = re.compile(r"\*\s*\([^)]*\)\s*\*")


def speakable(text: str) -> str:
    """Clean a beat for espeak: no markup, emoji, or symbols it would read aloud.
    VOICE DIRECTION contract (speech.py SYS_PROMPT): *sigh* is a breathing
    pause the prosody drags low — the WORD 'sigh' must never reach espeak,
    so this runs before the generic asterisk-strip."""
    t = _EMOJI.sub("", text)
    t = _STAGE_DIR.sub(" ", t)               # *(narrated action)* is never spoken
    t = re.sub(r"\*\s*sighs?\s*\*", "  ...  ", t, flags=re.I)
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


# (6.3) prosody: fixed voice, per-beat LEANING. Tiny deterministic tilts of
# espeak speed/pitch per beat, keyed off the beat's own markers — alarms run
# slightly faster, trailing "..." sighs drag, questions lift a touch. Still
# espeak-ng, NOT an AI voice; the ranges are narrow on purpose.
_PRO_WORDS = re.compile(
    r"\b(fuck|damn|hell|no|wait|wrong|fire|dead|crash|now|stop|overheat)\b", re.I)
_SLOW_WORDS = re.compile(
    r"\b(tired|whatever|fine|guess|meh|numb|later|whatever)\b|\.{3,}\s*$", re.I)


def _prosody(text: str) -> tuple:
    """(speed, pitch) deltas for one beat."""
    if not config.TTS_PROSODY:
        return (0, 0)
    jitter = (hash(text) % 7) - 3          # -3..+3, stable per text
    speed, pitch = jitter, jitter // 2
    if _PRO_WORDS.search(text):
        speed, pitch = 18, 4               # alarm: faster and higher
    if _SLOW_WORDS.search(text):
        speed, pitch = -12, -3             # tired/sigh: slower and flatter
    if text.rstrip().endswith("?"):
        pitch += 3
    return (max(-25, min(30, speed)), max(-8, min(8, pitch)))


# ------------------------------------------------------------ expressive voice
#
# Measured on espeak-ng 1.51 (same sentence, autocorrelation F0): speed and pitch
# only SHIFT her voice (pitch spread stayed ~35 Hz whatever I set). The thing that
# makes a voice sound flat or alive is INTONATION RANGE, an SSML <prosody range>:
# 0 % -> ~1 Hz spread (dead monotone), 100 % -> ~35 Hz (default), 200 % -> ~70 Hz
# (animated). That's the main lever below; speed/pitch/loudness ride along.
#
# The mood comes from the face she is making for that beat (her EYES carry the
# emotion, the mouth refines it), so voice and face can't disagree.
#   speed/pitch: deltas on the base REMIX_TTS_SPEED/PITCH   amp: espeak -a delta
#   range: intonation range in % (eyes set it; a mouth nudges it with range_delta)
#   gap: extra silence after the beat (s)
#   tremble/muffle: DSP amounts 0..1   stutter: flustered first-word repeat
_EYE_VOICE = {
    "default":   dict(),
    "anger":     dict(speed=14, pitch=-3, amp=30, range=140),
    "bottomlid": dict(speed=-10, pitch=3, range=175),               # smug: sing-song
    "really":    dict(speed=-8, pitch=-6, amp=-10, range=18),       # deadpan: flat
    "spooked":   dict(speed=24, pitch=10, amp=15, range=190, tremble=0.35),
    "tired":     dict(speed=-24, pitch=-7, amp=-35, range=45, muffle=0.6, gap=0.25),
    "tsun":      dict(speed=18, pitch=7, amp=5, range=180, stutter=True),
}
_MOUTH_VOICE = {
    "default": dict(),
    "anger":   dict(amp=10),
    "smirk":   dict(pitch=2),
    "pout":    dict(speed=-6, amp=-10, range_delta=-6),             # grumbling
    "trauma":  dict(speed=-8, pitch=-3, range_delta=-8), 
    "tsun":    dict(speed=4),
    "none":    dict(amp=-10),                                       # stunned
}
_NEUTRAL_VOICE = dict(speed=0, pitch=0, amp=0, range=100, gap=0.0,
                      tremble=0.0, muffle=0.0, stutter=False)


def voice_for(face) -> dict:
    """The voice profile for a Face (or an (eyes, mouth) pair); neutral if unknown.
    Everything leans by REMIX_TTS_EXPRESSIVENESS (1.0 = as designed)."""
    v = dict(_NEUTRAL_VOICE)
    e = config.TTS_EXPRESSIVENESS
    if face is None or not config.TTS_EXPRESSIVE:
        return v
    eyes, mouth = getattr(face, "eyes", None), getattr(face, "mouth", None)
    if eyes is None and isinstance(face, (tuple, list)) and len(face) == 2:
        eyes, mouth = face
    for part in (_EYE_VOICE.get(eyes, {}), _MOUTH_VOICE.get(mouth, {})):
        for k, x in part.items():
            if k == "stutter":
                v["stutter"] = v["stutter"] or bool(x)
            elif k == "range":                 # absolute (eyes)
                v["range"] = x
            elif k == "range_delta":           # relative (mouth)
                v["range"] += x
            else:
                v[k] += x
    for k in ("speed", "pitch", "amp"):
        v[k] = v[k] * e
    v["range"] = max(5.0, min(200.0, 100.0 + (v["range"] - 100.0) * e))
    v["gap"] = v["gap"] * e
    v["tremble"] = max(0.0, min(1.0, v["tremble"] * e))
    v["muffle"] = max(0.0, min(1.0, v["muffle"] * e))
    return v


def strain_from(snap: dict | None) -> float:
    """0..1 how strained the real machine is: hot (70 -> 90 C) or CPU pegged
    (85 -> 100 %). 0 when REMIX_TTS_STRAIN is off or the vitals are unknown."""
    if not config.TTS_STRAIN or not snap:
        return 0.0
    out = 0.0
    try:
        if snap.get("temp") is not None:
            out = max(out, (float(snap["temp"]) - 70.0) / 20.0)
        if snap.get("cpu") is not None:
            out = max(out, (float(snap["cpu"]) - 85.0) / 15.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, out))


def _punct(text: str) -> tuple:
    """(speed, amp, range) deltas from how the beat ENDS: '!' pushes, '?' lifts."""
    t = text.rstrip()
    if t.endswith("!"):
        return (4, 15, 25)
    if t.endswith("?"):
        return (0, 0, 20)
    return (0, 0, 0)


_SIGH_RE = re.compile(r"\*sighs?\*|\bsighs?\b", re.I)
# private-use markers carried through speakable() (it strips none of these)
_EM_OPEN, _EM_CLOSE, _ELLIPSIS, _DASH = "\ue001", "\ue002", "\ue003", "\ue004"


def _stutter(spoken: str, seed_text: str) -> str:
    """Flustered: ONE first-word repeat per beat ('it's' -> 'i-it's'), on roughly
    two beats in three. Deterministic per text."""
    if zlib.crc32(seed_text.encode()) % 3 == 0:
        return spoken
    m = re.match(r"^([\ue001-\ue004]*)([A-Za-z])([A-Za-z']+)", spoken)
    if not m:
        return spoken
    return f"{m.group(1)}{m.group(2)}-{m.group(2)}{m.group(3)}" + spoken[m.end():]


def _markup(text: str, stutter: bool = False) -> str:
    """Raw beat -> SSML body ('' = nothing to say). *word* and ALL-CAPS get
    emphasis, '...' becomes a real pause, dashes a short one. Everything else
    is speakable() exactly as before (and XML-escaped)."""
    # the *sigh* cue is a BREATH, not a word — and it must be turned into a
    # pause BEFORE the emphasis regex sees it (else she'd emphasize and then
    # speak the literal word "sigh"; that's exactly what happened on live
    # voice 2026-11-08 19:0x). The engine just pauses there; prosody waits.
    text = _STAGE_DIR.sub(" ", text)          # before the emphasis regex can bless it
    text = re.sub(r"\*\s*sighs?\s*\*", _ELLIPSIS, text, flags=re.I)
    t = re.sub(r"\*([^*\n]{1,40})\*", _EM_OPEN + r"\1" + _EM_CLOSE, text)
    t = re.sub(r"\.{2,}|…", _ELLIPSIS, t)
    t = re.sub(r"\s[—–-]\s|—", _DASH, t)
    spoken = speakable(t)
    if not re.sub(r"[\ue001-\ue004\s,.!?;:]", "", spoken):
        return ""
    if stutter:
        spoken = _stutter(spoken, text)
    spoken = html.escape(spoken, quote=False)
    spoken = re.sub(r"\b([A-Z]{2,})\b", _EM_OPEN + r"\1" + _EM_CLOSE, spoken)
    out, depth = [], 0
    for ch in spoken:                       # flatten nested emphasis to one level
        if ch == _EM_OPEN:
            if depth == 0:
                out.append('<emphasis level="strong">')
            depth += 1
        elif ch == _EM_CLOSE:
            depth = max(0, depth - 1)
            if depth == 0:
                out.append("</emphasis>")
        elif ch == _ELLIPSIS:
            out.append('<break time="380ms"/>')
        elif ch == _DASH:
            out.append('<break time="200ms"/>')
        else:
            out.append(ch)
    if depth:
        out.append("</emphasis>")
    return "".join(out).strip()


def _is_aside(text: str) -> bool:
    """A whole beat in (parentheses) is muttered under her breath: whispered."""
    t = text.strip().rstrip(".!?… ")
    return len(t) > 2 and t.startswith("(") and t.endswith(")")


def _variant(base_voice: str, variant: str) -> str:
    return base_voice.split("+")[0] + "+" + variant


def _wav_bytes(ch: int, width: int, rate: int, frames: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


async def _espeak(args: list, text: str) -> bytes | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            config.TTS_BIN, "--stdin", "--stdout", *args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(text.encode()), 15.0)
    except (OSError, asyncio.TimeoutError) as e:
        print(f"remix-brain: tts failed ({e!r})")
        return None
    return out if proc.returncode == 0 and len(out) > 44 else None


async def _synth_legacy(text: str) -> bytes | None:
    """The original fixed voice (REMIX_TTS_EXPRESSIVE=0)."""
    spoken = speakable(text)
    if not spoken:
        return None
    ds, dp = _prosody(text)
    return await _espeak([
        "-v", config.TTS_VOICE,
        "-s", str(max(80, config.TTS_SPEED + ds)),
        "-p", str(max(0, min(99, config.TTS_PITCH + dp))),
        "-a", str(config.TTS_AMP)], spoken)


async def _synth_one(text: str, face=None, strain: float = 0.0):
    """One beat -> (WAV bytes | None, extra silence after it in seconds)."""
    if not config.TTS_EXPRESSIVE:
        return await _synth_legacy(text), 0.0
    sigh = bool(_SIGH_RE.search(text))
    body = _SIGH_RE.sub(" ", text) if sigh else text
    v = voice_for(face)
    ds, dp = _prosody(body)
    ps, pa, pr = _punct(body)
    strain_fx = max(0.0, (strain - 0.35) / 0.65)            # nothing until it's really straining
    speed = v["speed"] + ds + ps + 8.0 * strain_fx
    pitch = v["pitch"] + dp
    amp = v["amp"] + pa
    rng = max(5.0, min(200.0, v["range"] + pr))
    markup = _markup(body, stutter=v["stutter"])
    wav = None
    sr = 22050
    if markup:
        voice = _variant(config.TTS_VOICE, "whisperf") if _is_aside(body) else config.TTS_VOICE
        ssml = f'<speak><prosody range="{int(round(rng))}%">{markup}</prosody></speak>'
        wav = await _espeak([
            "-m", "-v", voice,
            "-s", str(int(max(80, min(320, config.TTS_SPEED + speed)))),
            "-p", str(int(max(0, min(99, config.TTS_PITCH + pitch)))),
            "-a", str(int(max(20, min(200, config.TTS_AMP + amp))))], ssml)
    if wav is None and not sigh:
        return None, 0.0
    if wav is not None:
        ch, width, sr, frames = _frames(wav)
        if config.TTS_DSP:
            frames = vocal_fx.apply(frames, sr, v["tremble"], v["muffle"], strain_fx,
                                    seed=zlib.crc32(text.encode()))
    else:
        ch, width, frames = 1, 2, b""
    if sigh:
        breath = vocal_fx.to_pcm16(vocal_fx.sigh(sr, seed=zlib.crc32(text.encode())))
        frames = breath + (b"\x00\x00" * int(sr * 0.08)) + frames
    return _wav_bytes(ch, width, sr, frames), v["gap"]


def _frames(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return (w.getnchannels(), w.getsampwidth(), w.getframerate(),
                w.readframes(w.getnframes()))


async def synthesize(beats: list, tag: str, faces: list | None = None,
                     strain: float = 0.0) -> Speech | None:
    """Beats -> one WAV + beat start times. None = no voice (caller shows captions).
    `faces` (one per beat) colours each beat's voice; `strain` (0..1) is how hot
    the real machine is. Both optional: without them she speaks neutrally."""
    if not available() or not beats:
        return None
    faces = list(faces or [])
    faces += [None] * (len(beats) - len(faces))
    results = await asyncio.gather(
        *(_synth_one(b, faces[i], strain) for i, b in enumerate(beats)))
    pieces = []
    for wav, extra in results:
        pieces.append((_frames(wav) if wav else None, extra))
    fmt = next((p[0][:3] for p in pieces if p[0]), None)
    if fmt is None:
        return None
    ch, width, rate = fmt
    unit = rate * ch * width
    body = bytearray()
    starts, durs = [], []
    for p, extra in pieces:
        starts.append(len(body) / unit)
        if p and p[:3] == fmt:
            body.extend(p[3])
            durs.append(len(p[3]) / unit)
        else:
            durs.append(0.0)
        body.extend(b"\x00" * (int(rate * (config.TTS_BEAT_GAP_S + extra)) * ch * width))
    d = Path(config.TTS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    _prune(d)
    path = d / f"{tag}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(body))
    return Speech(str(path), starts, durs, len(body) / unit)


def _prune(d: Path, keep: int = 6) -> None:
    files = sorted(d.glob("*.wav"), key=lambda f: f.stat().st_mtime)
    for f in files[:-keep] if len(files) > keep else []:
        try:
            f.unlink()
        except OSError:
            pass
