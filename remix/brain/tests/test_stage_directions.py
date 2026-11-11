# test_stage_directions.py — narrated face/action lines must never be spoken,
# shown, or journaled. Cases are the real leaks from the Nov 10 journal.
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from brain import speech, tts  # noqa: E402

LEAKS = {
    "*(the red bars twitch)*\n\n...Whatever. Eight. Big deal.": "...Whatever. Eight. Big deal.",
    "*(the two tall red bars lean together into an aggressive squint)*\n\nWho else is going to do it? You?":
        "Who else is going to do it? You?",
    "...ugh. *(red bars widen slightly, the middle one flattening out into a tight little line)*  Fine.":
        "...ugh. Fine.",
    "*(red bars narrow into a glare)*  No.": "No.",
}
KEEP = [
    "That is *not* happening.",
    "*sigh* Fine.",
    "(whatever.) ...fine.",
    "Go wash your hands first. REALLY.",
]


class StageDirectionTests(unittest.TestCase):
    def test_strips_real_leaks(self):
        for raw, want in LEAKS.items():
            self.assertEqual(speech.strip_stage_directions(raw), want)

    def test_keeps_voice_marks(self):
        for line in KEEP:
            self.assertEqual(speech.strip_stage_directions(line), line)

    def test_long_asterisk_span_is_a_direction(self):
        self.assertEqual(speech.strip_stage_directions("*rolls her eyes slowly* Fine."), "Fine.")

    def test_clean_line_applies_it(self):
        self.assertEqual(speech._clean_line("*(red bars narrow into a glare)* No."), "No.")
        self.assertIsNone(speech._clean_line("*(the bars twitch)*"))

    def test_tts_never_speaks_them(self):
        for raw in LEAKS:
            spoken = tts.speakable(raw).lower()
            self.assertNotIn("bar", spoken)
            self.assertNotIn("twitch", spoken)
        self.assertNotIn("bar", tts._markup("*(red bars narrow into a glare)* No.").lower())

    def test_prompt_forbids_narration(self):
        p = speech.SYS_PROMPT
        self.assertIn("never describe or act out your face or body", p)
        self.assertIn("asterisks wrap a single word", p)


if __name__ == "__main__":
    unittest.main()
