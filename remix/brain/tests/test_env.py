# test_env.py — the .env loader contract (config._load_dotenv):
#   * every var defined ONCE; duplicates are named out loud, first wins
#   * values never clobber variables already present in the real environment
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from brain import config  # noqa: E402

_K = "_REMIX_TEST_ENV_VAR"      # random enough to never collide for real


def _load(text: str) -> list:
    """Point the loader's file at a temp dir, run it, return captured notes."""
    d = tempfile.mkdtemp()
    saved = config.BRAIN_DIR
    config.BRAIN_DIR = Path(d) / "brain"
    # _load_dotenv reads BRAIN_DIR.parent/.env -> tempdir/.env
    with open(Path(d) / ".env", "w") as f:
        f.write(text)
    notes = []
    with mock.patch.object(config, "print",
                           lambda *a, **k: notes.append(" ".join(map(str, a)))):
        config._load_dotenv()
    config.BRAIN_DIR = saved
    return notes


class LoaderTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop(_K, None)

    def tearDown(self):
        os.environ.pop(_K, None)

    def test_duplicate_definitions_first_wins_and_warns(self):
        notes = _load(f"{_K}=first\n{_K}=second\n")
        self.assertEqual("first", os.environ[_K])
        self.assertTrue(any("defined more than once" in n for n in notes))
        self.assertTrue(any(_K in n for n in notes))

    def test_real_environment_wins_over_env_file(self):
        os.environ[_K] = "from real env"
        _load(f"{_K}=from file\n")
        self.assertEqual("from real env", os.environ[_K])

    def test_names_without_value_are_fine(self):
        notes = _load("NOT_A_SETTING=\nSOME_JUST_A_NAME\n")
        self.assertFalse(any("more than once" in n for n in notes))
