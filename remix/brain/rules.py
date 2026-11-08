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

from . import audio, config

# An i5-4310M laptop idles warm, so thresholds skew forgiving; confirm on the
# real host and tune via env (config.py). Pose names match face_config.gd.
POSE_DEFAULT = "default"
TWITCH_POSES = {"anger", "pout"}  # face_config.gd allows twitch there

# Flavor text: dry, short, no cutesy AI-voice lines (user rule, 2026-11-08).
# Only ever SHOWN while the machine is quiet — see the gate in _idle_life.
MOCK_NOTICES = [
    "// fan's droning. business as usual //",
    "// all quiet on the driver front //",
    "// cpu nominal. nothing to file a complaint about //",
]


class RuleBrain:
    def __init__(self, available_poses=None):
        self.prior = {}              # previous vitals snapshot (for deltas)
        self.poses = list(available_poses or [])
        self.pose = "default"
        self.candidate = None
        self.candidate_count = 0
        self.candidate_look = None
        self.last_switch = 0.0
        now = time.monotonic()
        self.last_look_at = now
        self.last_notice_at = now   # no notices in the first quiet minutes
        self.last_twitch_at = now
        self.speak_gate_until = 0.0  # pause silent posing while she talks

    # ------------------------------------------------------------------ API

    def set_available_poses(self, ready_poses) -> None:
        self.poses = list(ready_poses or [])

    def baseline_message(self) -> dict:
        """Her current vitals-driven face, restated (to settle after speaking)."""
        return {"type": "pose", "pose": self.pose}

    def pause_for_speech(self, seconds: float) -> None:
        self.speak_gate_until = time.monotonic() + max(0.0, seconds)

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

        target, look, alarm = self._pick(cpu, ram, temp, battery, d_cpu)
        if alarm or self._change_earned(target, now):
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
        elif self.candidate != target:
            self.candidate = target
            self.candidate_count = 0

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
                out.append({"type": "look", "look": list(random.choice(pool))})
                self.last_look_at = now
        if (self.pose in TWITCH_POSES
                and now - self.last_twitch_at > random.uniform(18.0, 40.0)):
            out.append({"type": "twitch"})
            self.last_twitch_at = now
        if (now - self.last_notice_at > config.NOTICE_MIN_GAP_S
                and random.random() < 0.02):
            # Attempt counts even when suppressed: at most one try per gap.
            self.last_notice_at = now
            # Flavor holds under Do-Not-Disturb (fullscreen = can't be seen
            # either), and shows only when the machine is quiet anyway
            # (muted or low volume). Functional lines (transcript, "one sec",
            # ear problems) are exempt: those never go through here.
            if (not (aware or {}).get("fullscreen")
                    and (not config.NOTICE_GATE or audio.quiet())):
                pool = self._notice_pool(snap)
                if pool:
                    out.append({"type": "notice", "text": random.choice(pool),
                                "seconds": 3.0})
        return out

    def _look_pool(self, cpu):
        if self.pose == "spooked":
            return [[0.8, 0.1], [-0.8, 0.1], [0.0, -0.9], [0.0, 0.9]]
        if self.pose == "tired":
            return [[0.3, 0.6], [-0.3, 0.6]]
        if self.pose == "anger":
            return [[0.0, 0.0], [0.4, -0.2]]
        if (cpu is not None and cpu >= config.CPU_HIGH) or self.pose == "pout":
            return [[0.7, 0.2]]
        return [[random.uniform(-0.9, 0.9), random.uniform(-0.6, 0.6)]]

    def _notice_pool(self, snap):
        disk = _f(snap.get("disk"))
        if disk is not None and disk >= config.DISK_FULL:
            return ["// disk's %d%% full. that's a to-do //"
                    % round(disk)]
        battery = _f(snap.get("battery"))
        if battery is not None and battery <= config.BATT_LOW:
            return ["// battery at %d%%. fine. totally fine //"
                    % round(battery)]
        if self.pose == "spooked":
            return ["// that was a spike, not a fire. probably //"]
        return MOCK_NOTICES


def _f(v):
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
