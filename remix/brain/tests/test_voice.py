# test_voice.py — the expressive (still non-AI) voice: mood profiles, markup,
# DSP, and real-espeak measurements that the moods actually SOUND different.
import asyncio
import io
import os
import sys
import unittest
import wave
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from brain import config, faces, tts, vocal_fx  # noqa: E402

P = faces.POSES


def run(coro):
    return asyncio.run(coro)


def f0_spread(x, sr):
    """(median, std) of F0 by autocorrelation over voiced 40 ms windows."""
    out, hop, win = [], int(sr * 0.02), int(sr * 0.04)
    for i in range(0, len(x) - win, hop):
        s = x[i:i + win]
        if np.sqrt((s ** 2).mean()) < 0.02:
            continue
        s = s - s.mean()
        ac = np.correlate(s, s, "full")[win - 1:]
        lo, hi = int(sr / 400), int(sr / 70)
        if ac[0] <= 0:
            continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] / ac[0] > 0.3:
            out.append(sr / k)
    return (float(np.median(out)), float(np.std(out)), len(out)) if out else (0.0, 0.0, 0)


def load(path):
    w = wave.open(path)
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32768
    return x, sr


class ProfileTests(unittest.TestCase):
    def test_unknown_or_missing_face_is_neutral(self):
        self.assertEqual(tts._NEUTRAL_VOICE, tts.voice_for(None))
        self.assertEqual(tts._NEUTRAL_VOICE, tts.voice_for(faces.Face("nope", "nope")))
        self.assertEqual(tts._NEUTRAL_VOICE, tts.voice_for(P["default"]))

    def test_moods_lean_the_right_way(self):
        v = {k: tts.voice_for(f) for k, f in P.items()}
        self.assertGreater(v["tired"]["speed"] * -1, 15)           # slow
        self.assertLess(v["tired"]["amp"], 0)                      # quiet
        self.assertGreater(v["tired"]["muffle"], 0)                # muffled
        self.assertGreater(v["spooked"]["tremble"], 0)             # shaky
        self.assertGreater(v["spooked"]["speed"], v["anger"]["speed"] - 1)
        self.assertGreater(v["anger"]["amp"], v["default"]["amp"]) # louder
        self.assertLess(v["pout"]["range"], 25)                    # near-flat
        self.assertGreater(v["smirk"]["range"], 150)               # sing-song
        self.assertTrue(v["tsun"]["stutter"])
        self.assertFalse(v["anger"]["stutter"])

    def test_mouth_refines_the_eyes(self):
        eyes_only = tts.voice_for(faces.Face("really", "default"))
        with_pout = tts.voice_for(faces.Face("really", "pout"))
        self.assertLess(with_pout["range"], eyes_only["range"])
        self.assertLess(with_pout["speed"], eyes_only["speed"])

    def test_expressiveness_scales_and_clamps(self):
        full = tts.voice_for(P["spooked"])
        with mock.patch.object(config, "TTS_EXPRESSIVENESS", 0.5):
            half = tts.voice_for(P["spooked"])
        self.assertAlmostEqual(full["speed"] / 2, half["speed"])
        self.assertAlmostEqual(full["tremble"] / 2, half["tremble"])
        self.assertLess(abs(half["range"] - 100), abs(full["range"] - 100))
        with mock.patch.object(config, "TTS_EXPRESSIVENESS", 2.0):
            for f in P.values():
                v = tts.voice_for(f)
                self.assertTrue(5.0 <= v["range"] <= 200.0)
                self.assertTrue(0.0 <= v["tremble"] <= 1.0)
                self.assertTrue(0.0 <= v["muffle"] <= 1.0)

    def test_off_switch_is_neutral(self):
        with mock.patch.object(config, "TTS_EXPRESSIVE", False):
            self.assertEqual(tts._NEUTRAL_VOICE, tts.voice_for(P["spooked"]))

    def test_strain_comes_only_from_real_vitals(self):
        self.assertEqual(0.0, tts.strain_from(None))
        self.assertEqual(0.0, tts.strain_from({"temp": 55, "cpu": 20}))
        self.assertEqual(1.0, tts.strain_from({"temp": 95, "cpu": 10}))
        self.assertAlmostEqual(0.5, tts.strain_from({"temp": 40, "cpu": 92.5}))
        self.assertEqual(0.0, tts.strain_from({"temp": None, "cpu": None}))
        self.assertEqual(0.0, tts.strain_from({"temp": "hot"}))
        with mock.patch.object(config, "TTS_STRAIN", False):
            self.assertEqual(0.0, tts.strain_from({"temp": 99}))


class MarkupTests(unittest.TestCase):
    def test_star_emphasis_caps_emphasis_and_acronym_not_shouted(self):
        m = tts._markup("It is *really* NOT the cpu.")
        self.assertIn('<emphasis level="strong">really</emphasis>', m)
        self.assertIn('<emphasis level="strong">NOT</emphasis>', m)
        self.assertIn("C P U", m)
        self.assertNotIn("<emphasis level=\"strong\">C", m)

    def test_nested_emphasis_flattens(self):
        m = tts._markup("*NOW*")
        self.assertEqual(1, m.count("<emphasis"))
        self.assertEqual(1, m.count("</emphasis>"))

    def test_pauses(self):
        self.assertIn('<break time="380ms"/>', tts._markup("Fine... whatever."))
        self.assertIn('<break time="200ms"/>', tts._markup("it is hot - very hot"))

    def test_sigh_cue_is_a_breath_the_word_never_spoken(self):
        # The 19:0x live session: espeak said the literal word "sigh"
        # because the emphasis pass grabbed the cue first.
        m = tts._markup("*sigh* ...fine. Shutting up.")
        low = m.lower()
        self.assertNotIn("sigh", low)
        self.assertIn("fine", low)          # words after survive
        self.assertIn("break", low.lower()) # a pause remains to drag on
        self.assertEqual("", tts._markup("*sigh*          "))   # just a breath
        self.assertEqual("", tts.speakable("*sigh*          "))

    def test_xml_is_escaped(self):
        m = tts._markup("5 < 6 & 7 > 2")
        self.assertIn("&lt;", m)
        self.assertIn("&amp;", m)
        self.assertNotIn("<6", m)

    def test_nothing_to_say_is_empty(self):
        for t in ("", "...", "*", "😀", " , . "):
            self.assertEqual("", tts._markup(t), t)

    def test_no_private_markers_leak(self):
        m = tts._markup("*a*... b - c NO")
        for ch in "\ue001\ue002\ue003\ue004":
            self.assertNotIn(ch, m)

    def test_stutter_is_deterministic_once_and_opt_in(self):
        texts = [f"it is beat number {i}." for i in range(30)]
        outs = [tts._markup(t, stutter=True) for t in texts]
        self.assertEqual(outs, [tts._markup(t, stutter=True) for t in texts])
        hit = [o for o in outs if o.startswith("i-it")]
        self.assertTrue(0 < len(hit) < len(texts))                 # some, not all
        for o in hit:
            self.assertEqual(1, o.count("i-it"))
        self.assertFalse(any(tts._markup(t).startswith("i-it") for t in texts))

    def test_aside_and_variant_and_punct(self):
        self.assertTrue(tts._is_aside("(i did not say that.)"))
        self.assertFalse(tts._is_aside("well (maybe) not"))
        self.assertFalse(tts._is_aside("()"))
        self.assertEqual("en-us+whisperf", tts._variant("en-us+f3", "whisperf"))
        self.assertEqual("en-us+whisperf", tts._variant("en-us", "whisperf"))
        self.assertGreater(tts._punct("stop!")[1], 0)
        self.assertGreater(tts._punct("really?")[2], 0)
        self.assertEqual((0, 0, 0), tts._punct("fine."))

    def test_speakable_contract_is_untouched(self):
        self.assertEqual("53 degrees and 40 percent", tts.speakable("53C and 40%"))
        self.assertNotIn("*", tts.speakable("*shit*"))


class FxTests(unittest.TestCase):
    sr = 22050

    def tone(self, secs=0.6, hz=180.0):
        t = np.arange(int(self.sr * secs)) / self.sr
        return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)

    def test_length_dtype_and_no_clipping(self):
        x = self.tone()
        for y in (vocal_fx.tremble(x, self.sr, 1.0), vocal_fx.muffle(x, 1.0),
                  vocal_fx.crush(x, 1.0, self.sr, 1)):
            self.assertEqual(len(x), len(y))
            self.assertEqual(np.float32, y.dtype)
            self.assertLessEqual(float(np.max(np.abs(y))), 1.0)

    def test_zero_is_identity(self):
        x = self.tone()
        self.assertTrue(np.array_equal(x, vocal_fx.tremble(x, self.sr, 0.0)))
        self.assertTrue(np.array_equal(x, vocal_fx.crush(x, 0.0, self.sr)))
        pcm = vocal_fx.to_pcm16(x)
        self.assertEqual(pcm, vocal_fx.apply(pcm, self.sr))

    def test_tremble_wobbles_loudness(self):
        x = self.tone(1.0)
        def env_var(y):
            e = np.abs(y).reshape(-1, 441).max(axis=1)
            return float(np.var(e))
        self.assertGreater(env_var(vocal_fx.tremble(x, self.sr, 0.8)), env_var(x) + 1e-4)

    def test_muffle_removes_highs(self):
        rng = np.random.default_rng(0)
        x = rng.standard_normal(8000).astype(np.float32) * 0.2
        def hf(y):
            s = np.abs(np.fft.rfft(y))
            return float(s[len(s) // 2:].sum())
        self.assertLess(hf(vocal_fx.muffle(x, 1.0)), hf(x) * 0.3)

    def test_crush_is_deterministic_and_changes_signal(self):
        x = self.tone()
        a = vocal_fx.crush(x, 0.9, self.sr, seed=3)
        self.assertTrue(np.array_equal(a, vocal_fx.crush(x, 0.9, self.sr, seed=3)))
        self.assertFalse(np.array_equal(a, x))

    def test_sigh_is_a_decaying_breath(self):
        s = vocal_fx.sigh(self.sr, seed=1, seconds=0.75, peak=0.2)
        self.assertEqual(int(self.sr * 0.75), len(s))
        self.assertAlmostEqual(0.2, float(np.max(np.abs(s))), places=2)
        h = len(s) // 2
        self.assertGreater(float(np.sum(s[:h] ** 2)), float(np.sum(s[h:] ** 2)) * 2)
        self.assertTrue(np.array_equal(s, vocal_fx.sigh(self.sr, seed=1)))
        self.assertFalse(np.array_equal(s, vocal_fx.sigh(self.sr, seed=2)))

    def test_pcm_roundtrip_clips_safely(self):
        loud = np.array([2.0, -2.0, 0.5], dtype=np.float32)
        back = vocal_fx.to_float(vocal_fx.to_pcm16(loud))
        self.assertLessEqual(float(np.max(np.abs(back))), 1.0)


@unittest.skipUnless(tts.available(), "espeak-ng not installed")
class SoundTests(unittest.TestCase):
    """Real espeak-ng: do the moods actually come out different?"""
    T = ["I told you the fan was dying.", "Nobody listens to me."]

    def speak(self, face, beats=None, **kw):
        sp = run(tts.synthesize(beats or self.T, "vt_" + str(face).replace("/", "_"), faces=[face] * len(beats or self.T), **kw))
        x, sr = load(sp.path)
        return sp, x, sr

    def test_tired_is_slower_and_quieter_than_spooked(self):
        st, xt, _ = self.speak(P["tired"])
        ss, xs, _ = self.speak(P["spooked"])
        self.assertGreater(st.total, ss.total * 1.3)
        self.assertLess(np.sqrt((xt ** 2).mean()), np.sqrt((xs ** 2).mean()))

    def test_deadpan_is_flat_and_smug_is_animated(self):
        _, xp, sr = self.speak(P["pout"])
        _, xm, _ = self.speak(P["smirk"])
        _, xd, _ = self.speak(P["default"])
        sd_flat, sd_smug, sd_def = f0_spread(xp, sr)[1], f0_spread(xm, sr)[1], f0_spread(xd, sr)[1]
        self.assertLess(sd_flat, sd_def * 0.4)
        self.assertGreater(sd_smug, sd_def * 1.3)

    def test_angry_is_louder_than_neutral(self):
        _, xa, _ = self.speak(P["anger"])
        _, xd, _ = self.speak(P["default"])
        self.assertGreater(np.sqrt((xa ** 2).mean()), np.sqrt((xd ** 2).mean()) * 1.15)

    def test_aside_is_whispered(self):
        _, xn, sr = self.speak(P["default"], ["I did not say that."])
        _, xw, _ = self.speak(P["default"], ["(I did not say that.)"])
        self.assertLess(f0_spread(xw, sr)[2], f0_spread(xn, sr)[2] * 0.5)   # far less voiced

    def test_sigh_alone_makes_a_breath(self):
        sp, x, _ = self.speak(P["tired"], ["*sigh*"])
        self.assertGreater(sp.durations[0], 0.6)
        self.assertGreater(float(np.max(np.abs(x))), 0.05)

    def test_sigh_precedes_words(self):
        a, _, _ = self.speak(P["default"], ["fine."])
        b, _, _ = self.speak(P["default"], ["*sigh* fine."])
        self.assertGreater(b.durations[0], a.durations[0] + 0.6)

    def test_strain_adds_grit_only_when_strained(self):
        _, calm, _ = self.speak(P["default"], strain=0.0)
        _, calm2, _ = self.speak(P["default"], strain=0.3)       # under the threshold
        _, hot, _ = self.speak(P["default"], strain=1.0)
        n = min(len(calm), len(calm2))
        self.assertTrue(np.allclose(calm[:n], calm2[:n]))
        self.assertFalse(len(hot) == len(calm) and np.allclose(hot[:len(calm)], calm))

    def _dsp_pair(self, face, **kw):
        with mock.patch.object(config, "TTS_DSP", False):
            _, off, _ = self.speak(face, **kw)
        with mock.patch.object(config, "TTS_DSP", True):
            _, on, _ = self.speak(face, **kw)
        return off, on

    def test_dsp_layer_changes_samples_not_timing(self):
        for face in (P["spooked"], P["tired"]):
            off, on = self._dsp_pair(face)
            self.assertEqual(len(off), len(on), face)           # same length: cues stay valid
            self.assertFalse(np.allclose(off, on), face)

    def test_tired_is_actually_muffled(self):
        off, on = self._dsp_pair(P["tired"])
        def highs(x):
            s = np.abs(np.fft.rfft(x))
            return float(s[len(s) // 3:].sum())
        self.assertLess(highs(on), highs(off) * 0.85)

    def test_strain_grit_is_the_dsp_not_just_speed(self):
        off, on = self._dsp_pair(P["default"], strain=1.0)
        self.assertEqual(len(off), len(on))
        self.assertFalse(np.allclose(off, on))
        off0, on0 = self._dsp_pair(P["default"], strain=0.0)
        self.assertTrue(np.allclose(off0, on0))                 # calm default: DSP is a no-op

    def test_beat_starts_are_consistent_and_tired_leaves_a_longer_gap(self):
        sp, _, _ = self.speak(P["tired"])
        self.assertEqual(len(self.T), len(sp.starts))
        self.assertEqual(0.0, sp.starts[0])
        gap = sp.starts[1] - (sp.starts[0] + sp.durations[0])
        self.assertGreater(gap, config.TTS_BEAT_GAP_S + 0.1)
        self.assertAlmostEqual(sp.total, sp.starts[1] + sp.durations[1] + config.TTS_BEAT_GAP_S + 0.25, delta=0.05)

    def test_old_voice_is_exactly_recoverable(self):
        with mock.patch.object(config, "TTS_EXPRESSIVE", False):
            wav, extra = run(tts._synth_one("Close some tabs, it's 53C.", P["spooked"]))
            self.assertEqual(0.0, extra)
            self.assertEqual(wav, run(tts._synth_legacy("Close some tabs, it's 53C.")))

    def test_special_characters_do_not_break_synthesis(self):
        sp, x, _ = self.speak(P["anger"], ["5 < 6 & 7 > 2!", "\"quote\" 'apos' <b>tag</b>"])
        self.assertGreater(sp.total, 1.0)

    def test_faceless_call_still_works(self):
        sp = run(tts.synthesize(["hello there."], "vt_nofaces"))
        self.assertIsNotNone(sp)
        self.assertEqual(1, len(sp.starts))


if __name__ == "__main__":
    unittest.main()
