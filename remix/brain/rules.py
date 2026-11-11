# rules.py — the RuleBrain (left-half, $0 fallback): snapshot -> pose decisions.
# Silent life uses ONLY this: no Jev calls, no LLM calls, ever. Jev is for
# reactions to user input / notable events only (budget policy in notes).
#
# Design rules that keep her watchable:
#   * A pose must earn a change: N consecutive snapshots + a minimum hold,
#     so one noisy CPU sample never flips her.
#   * Alarms (overheat, CPU spike) bypass the persistence gate — she should
#     LOOK startled immediately.
#   * Unknown/dropped sensors (temp/battery None) just miss their branches.
#
# Output contract: update(snap) returns brain->Godot messages (protocol v2:
# pose/look/notice/twitch per godot/PROTOCOL.md). Pose names validated
# against ready.poses from the face at runtime.
import random
import time

from . import config

# An i5-4310M laptop idles warm, so thresholds skew forgiving; confirm on the
# real host and tune via env (config.py). Pose names match face_config.gd.
POSE_DEFAULT = "default"
TWITCH_POSES = {"anger", "pout"}  # face_config.gd allows twitch there

# (Phase 2, audit #4: MOCK_NOTICES and the _notice_pool flavour lines are
# GONE. Pre-written lines in her voice contradicted the no-pools rule; her
# words now only ever come from the LLM through the normal path, or not at
# all. Battery-low and disk-full still reach her as real triggers/alarms —
# battery_low is a proactive trigger (spoken fresh, $0 pool), spooked pose
# and vitals posing are autonomic.)


class RuleBrain:
    def __init__(self, available_poses=None):
        self.prior = {}              # previous vitals snapshot (for deltas)
        self.poses = list(available_poses or [])
        self.pose = "default"
        self.candidate = None
        self.candidate_count = 0
        self.candidate_look = None
        self.last_switch = 0.0
        self._last_look = None   # (5) last look vector, for jump-size pacing
        now = time.monotonic()
        self.last_look_at = now
        self.last_twitch_at = now
        self.speak_gate_until = 0.0  # pause silent posing while she talks
        self.listen_gate_until = 0.0  # ... and while she listens to you (listening.py)
        self._listen_self_load = False

    # ------------------------------------------------------------------ API

    def set_available_poses(self, ready_poses) -> None:
        self.poses = list(ready_poses or [])

    def baseline_message(self) -> dict:
        """Her current vitals-driven face, restated (to settle after speaking)."""
        return {"type": "pose", "pose": self.pose}

    def pause_for_speech(self, seconds: float) -> None:
        self.speak_gate_until = time.monotonic() + max(0.0, seconds)

    def speaking(self) -> bool:
        """True while a reply (or chatter) is being performed."""
        return time.monotonic() < self.speak_gate_until

    def release_listening(self) -> None:
        """The line is over (sent, cleared, or abandoned)."""
        self.listen_gate_until = 0.0
        self._listen_self_load = False

    def hold_listening(self, seconds: float, self_load: bool = False) -> None:
        """She is listening to you: keep the silent life (drift, twitches,
        vitals-driven pose changes, notices) out of her face. Real alarms
        (overheat, pegged CPU) still get through.

        self_load: the load may be HER OWN (local Whisper transcribing partials
        while you hold the key), so a CPU-spike alarm is ignored; a real
        temperature alarm still passes."""
        self.listen_gate_until = max(self.listen_gate_until,
                                     time.monotonic() + max(0.0, seconds))
        self._listen_self_load = bool(self_load)

    def update(self, snap: dict, now=None, aware=None) -> list:
        """Feed one vitals snapshot (+ optional awareness state); return
        protocol v2 messages to send."""
        now = time.monotonic() if now is None else now
        out = []
        prev = self.prior
        self.prior = dict(snap)

        cpu = _f(snap.get("cpu"))
        ram = _f(snap.get("ram"))
        temp = _f(snap.get("temp"))
        battery = _f(snap.get("battery"))
        d_cpu = (cpu - prev.get("cpu")) if prev.get("cpu") is not None else 0.0

        if now < self.speak_gate_until:
            return []
        listening = now < self.listen_gate_until

        target, look, alarm = self._pick(cpu, ram, temp, battery, d_cpu)
        if (listening and self._listen_self_load and alarm
                and not (temp is not None and temp >= config.TEMP_ALARM_C)):
            target, look, alarm = None, None, False   # that spike is her own ears
        if alarm or (not listening and self._change_earned(target, now)):
            if target is not None and target != self.pose:
                self.pose = target
                self.last_switch = now
                self.candidate = None
                self.candidate_count = 0
                msg = {"type": "pose", "pose": self.pose}
                if look:
                    msg["look"] = list(look)
                    self.last_look_at = now
                out.append(msg)
        elif not listening and self.candidate != target:
            self.candidate = target
            self.candidate_count = 0

        if not listening:
            out.extend(self._idle_life(snap, cpu, now, aware))
        return out

    # ------------------------------------------------------------- internals

    def _pick(self, cpu, ram, temp, battery, d_cpu):
        """Return (target_pose|None, look|None, is_alarm)."""
        # Alarms: bypass persistence, react now.
        if temp is not None and temp >= config.TEMP_ALARM_C:
            return "spooked", [0.0, 0.1], True
        if cpu is not None and cpu >= config.CPU_SPIKE:
            return "spooked", [0.0, 0.4], True

        # Sustained states: candidates with a default gaze.
        if cpu is not None and cpu >= config.CPU_HIGH:
            return "anger", None, False
        if battery is not None and battery <= config.BATT_LOW:
            return "tired", [0.0, 0.5], False
        if battery is not None and battery <= config.BATT_DROOP and d_cpu >= 0:
            return "pout", None, False
        if cpu is not None and ram is not None and cpu <= config.CPU_FLAT and ram < 70.0:
            # Idle machine: mostly default, occasionally bored/tired for life.
            roll = random.random()
            if roll < 0.08:
                return "pout", [0.6, 0.2], False
            if roll < 0.14:
                return "tired", None, False
            if roll < 0.17:
                return "smirk", None, False
            return "default", None, False
        if cpu is None or ram is None:  # degenerate sensors -> harmless idle
            return "default", None, False
        return "default", None, False

    def _change_earned(self, target, now) -> bool:
        """Sustained-state changes need a candidate streak + minimum hold."""
        if target is None:
            return False
        if target != self.candidate:
            if self.candidate == self.pose:
                # target agrees with what we already are: reset the streak.
                self.candidate = target
                self.candidate_count = 0
            else:
                self.candidate = target
                self.candidate_count = 0
        else:
            self.candidate_count += 1
        return (self.candidate_count >= config.POSE_CANDIDATE_N
                and now - self.last_switch >= config.POSE_MIN_HOLD_S)

    def _idle_life(self, snap, cpu, now, aware=None) -> list:
        """Pose stays; drift gaze, twitch, rare notice."""
        out = []
        if now - self.last_look_at > random.uniform(6.0, 13.0):
            pool = self._look_pool(cpu)
            if pool:
                target = list(random.choice(pool))
                out.append({"type": "look", "look": target})
                # (5) pacing: a LARGE eye jump costs focus — bank some extra
                # quiet time before the next drift; small jumps keep tempo.
                prev = self._last_look
                self._last_look = target
                jump = 0.0 if prev is None else (
                    abs(target[0] - prev[0]) + abs(target[1] - prev[1]))
                self.last_look_at = now + jump * random.uniform(2.0, 5.0)
        if (self.pose in TWITCH_POSES
                and now - self.last_twitch_at > random.uniform(18.0, 40.0)):
            out.append({"type": "twitch"})
            self.last_twitch_at = now
        return out

    def _look_pool(self, cpu):
        """(5) boost: bigger amplitudes than the old pools. Cadence rules live
        in _idle_life: small jumps allowed more often, big jumps throttled."""
        if self.pose == "spooked":
            return [[0.95, 0.0], [-0.95, 0.0], [0.0, -0.85], [0.0, 0.85]]
        if self.pose == "tired":
            return [[0.45, 0.65], [-0.45, 0.65]]
        if self.pose == "anger":
            return [[0.0, 0.0], [0.55, -0.25], [-0.4, -0.1]]
        if (cpu is not None and cpu >= config.CPU_HIGH) or self.pose == "pout":
            return [[0.85, 0.25], [-0.85, 0.25]]
        return [[random.uniform(-0.9, 0.9), random.uniform(-0.7, 0.7)],
                [random.uniform(-0.5, 0.5), random.uniform(-0.4, 0.4)]]


def _f(v):
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
