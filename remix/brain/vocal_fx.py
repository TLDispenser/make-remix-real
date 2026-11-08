# vocal_fx.py — plain signal processing on her espeak voice. NOT an AI voice:
# no models anywhere, just arithmetic on samples (user rule: non-AI TTS).
#
# What espeak-ng can't do by itself, this adds, per beat and only as much as the
# mood asks for:
#   tremble  scared: pitch + loudness wobble (~7 Hz), like a shaky voice
#   muffle   tired/sulky: softens the highs, like talking into your sleeve
#   crush    strained hardware: sample-and-hold grit + tiny repeat-glitches
#   sigh     a synthesized breath (filtered noise), for "*sigh*"
#
# Every function is deterministic for a given seed, keeps the input length
# (except sigh, which makes a new clip), and never clips.
import numpy as np


def to_float(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0


def to_pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def _lowpass(x: np.ndarray, width: int) -> np.ndarray:
    """Hann-window moving average: a cheap, stable, vectorized low-pass."""
    if width <= 1 or len(x) == 0:
        return x
    k = np.hanning(width + 2)[1:-1].astype(np.float32)
    k /= k.sum()
    return np.convolve(x, k, mode="same").astype(np.float32)


def tremble(x: np.ndarray, sr: int, amount: float, rate: float = 7.0) -> np.ndarray:
    """Shaky voice. amount 0..1: up to ~4 % pitch wobble and ~35 % loudness wobble."""
    amount = float(np.clip(amount, 0.0, 1.0))
    n = len(x)
    if amount <= 0.0 or n < 8:
        return x
    rate = rate + 2.0 * amount
    idx = np.arange(n, dtype=np.float64)
    t = idx / sr
    depth = amount * 0.04 * sr / (2.0 * np.pi * rate)       # samples of delay swing
    y = np.interp(idx + depth * np.sin(2.0 * np.pi * rate * t), idx, x)
    y *= 1.0 - 0.35 * amount * (0.5 + 0.5 * np.sin(2.0 * np.pi * rate * t + 1.0))
    return y.astype(np.float32)


def muffle(x: np.ndarray, amount: float) -> np.ndarray:
    """Duller, closer voice. amount 0..1 -> low-pass width 1..9 samples."""
    amount = float(np.clip(amount, 0.0, 1.0))
    width = 1 + 2 * int(round(amount * 4))
    return _lowpass(x, width)


def crush(x: np.ndarray, amount: float, sr: int = 22050, seed: int = 0) -> np.ndarray:
    """Hardware-under-strain grit. amount 0..1: sample-and-hold, fewer bits, and
    now and then a 12 ms chunk repeats (a buffer glitch)."""
    amount = float(np.clip(amount, 0.0, 1.0))
    n = len(x)
    if amount <= 0.0 or n < 8:
        return x
    hold = 1 + int(amount * 4)
    y = np.repeat(x[::hold], hold)[:n].copy()
    levels = float(2 ** (13 - int(amount * 6)))
    y = np.round(y * levels) / levels
    rng = np.random.default_rng(seed)
    chunk = max(1, int(0.012 * sr))
    for i in range(chunk, n - chunk, chunk):
        if rng.random() < amount * 0.10:
            y[i:i + chunk] = y[i - chunk:i]
    return y.astype(np.float32)


def sigh(sr: int, seed: int = 0, seconds: float = 0.75, peak: float = 0.2) -> np.ndarray:
    """A breath out: noise that starts airy and darkens as it fades."""
    n = max(1, int(sr * seconds))
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n).astype(np.float32)
    bright, dark = _lowpass(noise, 3), _lowpass(noise, 15)
    mix = np.linspace(0.0, 1.0, n, dtype=np.float32) ** 0.7
    y = bright * (1.0 - mix) + dark * mix
    y /= max(1e-6, float(np.sqrt(np.mean(y * y))))
    t = np.arange(n, dtype=np.float32) / sr
    env = np.minimum(t / 0.12, 1.0) * np.exp(-t / 0.28)
    y = y * env
    y *= peak / max(1e-6, float(np.max(np.abs(y))))
    return y.astype(np.float32)


def apply(pcm16: bytes, sr: int, tremble_amt: float = 0.0, muffle_amt: float = 0.0,
          crush_amt: float = 0.0, seed: int = 0) -> bytes:
    """PCM16 mono in -> PCM16 mono out, same length. All-zero amounts = unchanged."""
    if tremble_amt <= 0 and muffle_amt <= 0 and crush_amt <= 0:
        return pcm16
    x = to_float(pcm16)
    if tremble_amt > 0:
        x = tremble(x, sr, tremble_amt)
    if muffle_amt > 0:
        x = muffle(x, muffle_amt)
    if crush_amt > 0:
        x = crush(x, crush_amt, sr, seed)
    return to_pcm16(x)
