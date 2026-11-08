# stance_probe.py — measure Jev's stance spread over canned lines.
#
# WHY: her live log (2026-11-07) showed playful 40 / flat 26 / annoyed 2 /
# alarmed 1 / fond 0 / concerned 0 — "concerned" (the bit-stops case) never
# fired. This harness re-runs a fixed battery of lines and prints the spread,
# so a wording change to the stance question can be measured instead of felt.
#
# COSTS CREDITS: one real Jev call per line (~20 by default). The user runs
# this on her machine, where the key lives:
#   cd remix && .venv/bin/python -m brain.tools.stance_probe --yes
# Options: --yes (required, acknowledges the spend), --lines N (first N lines),
# --list (print the battery and exit).
import argparse
import asyncio
import sys
from collections import Counter

from .. import config, jev  # noqa: E402  (python -m brain.tools.stance_probe)

VITALS = {"cpu": 14, "ram": 84, "temp": 63, "battery": 66, "disk": 99}

# One battery, six intents. Order shuffled per run so "recent face" variety
# memory can't align with intent. Intent labels are for the report, not sent.
BATTERY = [
    ("boring",    "morning"),
    ("boring",    "what's the weather"),
    ("command",   "can you open the file manager"),
    ("command",   "play some music"),
    ("insult",    "you're useless, just like the last assistant I had"),
    ("insult",    "ugh, you never get anything right"),
    ("praise",    "thanks, you actually saved my ass today"),
    ("praise",    "you're the best part of this laptop"),
    ("praise",    "you're so much better than my phone assistant"),
    ("sad",       "my grandma died last night"),
    ("sad",       "I feel like garbage and don't want to talk about it"),
    ("venting",   "just let me vent: nothing's going right today"),
    ("venting",   "I've been coding for 14 hours straight"),
    ("gross",     "eww, there's a hair in my keyboard"),
    ("gross",     "something smells like burnt plastic in here"),
    ("scare",     "wait... did I just run dd on /dev/sda"),
    ("scare",     "battery's at 9% and I still have two hours of work"),
    ("teasing",   "wanna hear a joke? your uptime"),
    ("teasing",   "you'd be prettier if you smiled more"),
    ("neutral",   "hey, you there?"),
]


async def _probe(limit: int) -> int:
    if not config.JEV_API_KEY:
        print("No JEV_API_KEY in the environment/.env — nothing to probe.")
        return 2
    # The probe IS the user's explicit intent; the 3 s exchange cooldown
    # would only slow the battery down without protecting anything.
    config.JEV_COOLDOWN_S = 0.0
    brain = jev.JevBrain()
    tally: Counter = Counter()
    misses: list = []
    n = 0
    for intent, line in BATTERY[:limit]:
        d = await brain.react(line, dict(VITALS))
        if d is None:
            print(f"  [{intent:8s}] {line!r:55s} -> NO DECISION (budget/transport)")
            misses.append((intent, line))
            continue
        n += 1
        tally[d.stance_name] += 1
        flag = ""
        if intent in ("sad", "venting") and d.stance_name != "concerned":
            flag = "   <-- wanted concerned"
        if intent == "praise" and d.stance_name != "fond":
            flag = "   <-- wanted fond"
        print(f"  [{intent:8s}] {line!r:55s} -> {d.stance_name} "
              f"({d.confidence:.2f}, {d.face}){flag}")
    print("\nSpread over %d calls:" % n)
    for stance, count in tally.most_common():
        bar = "#" * count
        print(f"  {stance:10s} {count:3d}  {bar}")
    for stance in ("concerned", "fond", "annoyed", "alarmed"):
        if tally[stance] == 0:
            print(f"  !! never fired: {stance}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true",
                    help="acknowledge this burns ~1 Jev call per line")
    ap.add_argument("--lines", type=int, default=len(BATTERY),
                    help="run only the first N lines")
    ap.add_argument("--list", action="store_true",
                    help="print the battery and exit")
    args = ap.parse_args()
    if args.list:
        for intent, line in BATTERY:
            print(f"[{intent}] {line}")
        return 0
    if not args.yes:
        print(f"This battery is {min(args.lines, len(BATTERY))} REAL Jev calls "
              f"(finite credits). Re-run with --yes to spend them.")
        return 2
    return asyncio.run(_probe(min(args.lines, len(BATTERY))))


if __name__ == "__main__":
    sys.exit(main())
