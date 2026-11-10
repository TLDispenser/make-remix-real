# stt.py — Phase 5 ears: push-to-talk capture + LOCAL transcription.
# Privacy rules (spec, non-negotiable):
#   * mic open ONLY while the key is held (hotkey client says start/stop)
#   * audio never touches disk: parec/pw-record stream raw PCM into memory
#   * transcription is 100% local (faster-whisper); only the final text
#     continues into the normal user_text pipeline
#   * no wake word, no continuous listening, ever
import asyncio
import os
import shlex
import shutil
import threading
import time
from typing import Callable, NamedTuple

from . import config

# whisper hallucination on silence, and general junk filters
_PHANTOM_TEXTS = {"thank you.", "thanks for watching!", "bye."}
_NS_DROP = 0.5        # no_speech_prob >= this -> phantom
_LP_DROP = -1.0       # avg_logprob <= this -> garbled

_RECORDER_CANDIDATES = ["pw-record", "parec", "arecord"]
# pw-record first: parec (PulseAudio-on-PipeWire 1.6.8) opens the default
# source and streams ZERO bytes on this host (2026-11-08 probe); pw-record
# is PipeWire-native and works. parec kept as fallback for other setups.


def _recorder_flags(name: str) -> list:
    if name.startswith("pw-"):  # pw-record/pw-cat (PipeWire)
        return ["--raw", "--format=s16", "--rate=16000", "--channels=1", "-"]
    if name.startswith("arecord"):  # ALSA CLI
        return ["-D", "pulse", "-f", "s16_le", "-c", "1", "-r", "16000",
                "-t", "raw", "-"]
    return ["--format=s16le", "--rate=16000", "--channels=1",  # parec(-ord)
            "--file-format=raw", "-"]


def _smart_split(raw: str) -> list:
    """argv split that survives executables whose paths contain spaces
    (this project lives in '.../code stuffs/...'): leading tokens rejoin
    greedily until one exists as a runnable file."""
    toks = raw.split(" ")
    acc = ""
    for i, tok in enumerate(toks):
        acc = tok if not acc else acc + " " + tok
        if os.path.isfile(acc) and os.access(acc, os.X_OK):
            return [acc, *toks[i + 1:]]
    return shlex.split(raw)


def recorder_cmd() -> list | None:
    """Mic capture command; flags baked in, 16 kHz mono s16le raw PCM on
    stdout. Resolves PTT_REC_CMD: 'auto' (find parec/pw-record/arecord) or
    a command/program string. Paths with spaces stay intact (project dir
    has spaces, so plain shlex.split is wrong here)."""
    raw = str(config.PTT_REC_CMD).strip()
    if raw == "auto":
        for cand in _RECORDER_CANDIDATES:
            found = shutil.which(cand)
            if found:
                return [found, *_recorder_flags(os.path.basename(found))]
        return None
    # explicit command: whole string may be a single path (spaces in dirs)
    if raw and os.path.isfile(raw) and os.access(raw, os.X_OK):
        return [raw, *_recorder_flags(os.path.basename(raw))]
    parts = _smart_split(raw)
    if not parts:
        return None
    if os.path.isfile(parts[0]) and os.access(parts[0], os.X_OK):
        return parts  # argv[0] + args, caller owns the flags
    if shutil.which(parts[0]):  # bare binary name + args
        return [shutil.which(parts[0]), *parts[1:]]
    return None


class HoldResult(NamedTuple):
    text: str      # transcript, "" when nothing useful
    reason: str    # ok | short | silence | unclear | no_mic | error


def clean_segments(segments) -> str:
    """Filter whisper phantoms/junk, join the survivors."""
    out = []
    for s in segments:
        txt = (getattr(s, "text", "") or "").strip()
        if not txt:
            continue
        if txt.lower() in _PHANTOM_TEXTS:
            continue
        nsp = float(getattr(s, "no_speech_prob", 0.0))
        lp = float(getattr(s, "avg_logprob", 0.0))
        if nsp >= _NS_DROP or lp <= _LP_DROP:
            continue
        out.append(txt)
    return " ".join(out)


class PushToTalk:
    """One held-key recording; thread guards + a watchdog for missed key-up."""

    def __init__(self, on_status: Callable | None = None,
                 on_timeout: Callable | None = None,
                 on_partial: Callable | None = None) -> None:
        self.on_status = on_status
        self.on_timeout = on_timeout
        # on_partial(text): called (in the event loop) with what has been said
        # so far, every LISTEN_STT_INTERVAL_S while the key is held. Local
        # Whisper only; nothing leaves the machine. See listening.py.
        self.on_partial = on_partial
        self._partial_task: asyncio.Task | None = None
        self._proc = None
        self._hold_task: asyncio.Task | None = None
        self._pcm = bytearray()
        self._started_at = 0.0
        self._last: HoldResult | None = None
        self._model = None
        self._warming = False
        self._status = "warming"   # warming | ready | error
        self._closed = asyncio.Event()

    # ------------------------------------------------------------ properties

    @property
    def status(self) -> str:
        """warming | ready | error | no_mic (main reads this to hint UX)."""
        return self._status

    @property
    def listening(self) -> bool:
        return self._proc is not None and not self._closed.is_set()

    # ---------------------------------------------------------------- flow

    async def start(self) -> str:
        if self.listening:
            return "ok"
        self._closed.clear()
        self._pcm.clear()
        self._last = None
        cmd = recorder_cmd()
        if not cmd:
            self._status = "no_mic"
            return "no_mic"
        try:
            self._proc = await asyncio.create_subprocess_exec(
                cmd[0], *cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
        except (OSError, FileNotFoundError) as e:
            print(f"remix-brain: could not open mic ({e!r})")
            self._proc = None
            return "no_mic"
        self._started_at = time.monotonic()
        asyncio.create_task(self._drain())
        self._hold_task = asyncio.create_task(self._watchdog())
        if self.on_partial and config.LISTEN_STT_PARTIALS:
            self._partial_task = asyncio.create_task(self._partial_loop())
        return "ok"

    # ------------------------------------------------------------- partials

    async def _partial_loop(self) -> None:
        """While the key is held, transcribe what has been said so far and
        hand each CHANGED result to on_partial. Best effort: any failure just
        ends the partials; the final transcript at key-up is unaffected."""
        last = ""
        try:
            while self.listening:
                await asyncio.sleep(max(0.2, config.LISTEN_STT_INTERVAL_S))
                if not self.listening or self._model is None:
                    continue          # key up, or the model is still warming
                text = await self._partial_once()
                if text and text != last and self.listening and self.on_partial:
                    last = text
                    self.on_partial(text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"remix-brain: ptt partials stopped ({e!r})")

    async def _partial_once(self) -> str:
        raw = bytes(self._pcm)
        raw = raw[: len(raw) // 2 * 2]                 # whole samples only
        if len(raw) < config.LISTEN_STT_MIN_S * 32000:  # 16 kHz s16le mono
            return ""
        import numpy as np
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if float(np.abs(pcm).max(initial=0.0)) < config.PTT_MIN_PEAK:
            return ""
        model = self._model

        def _run() -> str:
            segs, _info = model.transcribe(
                pcm, language=config.PTT_LANGUAGE or "en", vad_filter=True,
                condition_on_previous_text=False, beam_size=1)
            return clean_segments(list(segs))

        return await asyncio.to_thread(_run)

    def _stop_partials(self) -> None:
        if self._partial_task is not None:
            self._partial_task.cancel()
            self._partial_task = None

    async def _drain(self) -> None:
        assert self._proc is not None
        try:
            while True:
                chunk = await self._proc.stdout.read(16384)
                if not chunk:
                    break
                self._pcm.extend(chunk)
        except Exception:
            pass

    async def _watchdog(self) -> None:
        """A missed key-up must still close the mic (never stays listening)."""
        try:
            await asyncio.wait_for(self._closed.wait(),
                                   timeout=config.PTT_MAX_PRESS_S)
            return  # key-up arrived: normal close, no timeout
        except asyncio.TimeoutError:
            pass
        print("remix-brain: ptt key-up missed - watchdog closes the mic")
        self._hold_task = None  # it's ME; don't self-cancel in _finalize
        await self._finalize()
        if self.on_timeout:
            try:
                self.on_timeout()
            except Exception:
                pass

    async def stop(self) -> HoldResult:
        if self.listening:
            self._last = await self._finalize()
        return self._last or HoldResult("", "no_mic")

    async def cancel(self) -> None:
        """Shutdown path: never leave the mic open."""
        self._stop_partials()
        if self._proc is not None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=1.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                if self._proc is not None:
                    self._proc.kill()
        self._proc = None
        self._closed.set()

    async def _finalize(self) -> HoldResult:
        me = asyncio.current_task()
        self._stop_partials()
        if self._hold_task is not None and self._hold_task is not me:
            self._hold_task.cancel()
        self._hold_task = None
        proc, self._proc = self._proc, None
        self._closed.set()
        dur = time.monotonic() - self._started_at
        if proc is not None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
            except Exception:
                pass
        # drain task still holds the tail after EOF; give it one beat
        await asyncio.sleep(0.05)
        chunks = bytes(self._pcm)
        chunks = chunks[: len(chunks) // 2 * 2]   # a read can end mid-sample
        self._pcm.clear()  # audio dropped after use (never stored, ever)

        if dur < config.PTT_MIN_PRESS_S:
            return HoldResult("", "short")
        if not chunks:
            return HoldResult("", "silence")

        import numpy as np
        pcm = np.frombuffer(chunks, dtype=np.int16).astype(np.float32) / 32768.0
        if float(np.abs(pcm).max(initial=0.0)) < config.PTT_MIN_PEAK:
            print("remix-brain: ptt: peak below floor - silence, model skipped")
            return HoldResult("", "silence")

        if self._model is None:
            self._warm_sync()  # user spoke while warmup was still pending
        t0 = time.monotonic()
        try:
            segments, _info = self._model.transcribe(
                pcm,
                language=config.PTT_LANGUAGE or "en",
                vad_filter=True,
                condition_on_previous_text=False,
            )
            text = await asyncio.to_thread(_consume, segments)
        except Exception as e:
            print(f"remix-brain: whisper failed ({e!r})")
            return HoldResult("", "error")
        took = time.monotonic() - t0
        if not text:
            print(f"remix-brain: whisper said nothing usable ({took:.1f}s)")
            return HoldResult("", "unclear")
        print(f"remix-brain: heard \"{text}\" ({dur:.1f}s held, "
              f"{took:.1f}s local transcribe)")
        return HoldResult(text, "ok")

    # ----------------------------------------------------------- whisper ops

    def warm(self) -> None:
        """Load whisper once, in a background thread (spec: no slow first use)."""
        if self._model is not None or self._warming:
            return
        self._warming = True
        threading.Thread(target=self._try_warm, daemon=True,
                         name="stt-warm").start()

    def _try_warm(self) -> None:
        try:
            self._warm_sync()
            self._status = "ready"
            if self.on_status:
                self.on_status("ready")
        except Exception as e:
            self._status = "error"
            print(f"remix-brain: whisper model failed to load ({e!r}) - "
                  f"ears offline")
            if self.on_status:
                self.on_status("error")

    def _warm_sync(self) -> None:
        if self._model is not None:
            return
        t0 = time.monotonic()
        from faster_whisper import WhisperModel
        self._model = WhisperModel(config.PTT_MODEL, device="cpu",
                                   compute_type="int8")
        print(f"remix-brain: whisper {config.PTT_MODEL!r} warm in "
              f"{time.monotonic() - t0:.1f}s (local, no API)")


def _consume(segments) -> str:
    """Materialize the streaming generator (run in a worker thread)."""
    return clean_segments(list(segments))
