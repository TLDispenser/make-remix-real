# Test package. The suite must never touch the live brain's files, so the
# decision log is switched off here, once, before any test module loads
# (individual tests may re-enable it explicitly).
from brain import config as _config

_config.DECISIONS_LOG = False
# Same principle, privacy edition (2026-11-15): the tests encode the DEFAULT
# .env.example contract, but config imports the OPERATOR'S live .env — so her
# REMIX_TITLES=scrub made titles flow through redaction tests and her real
# API key made "unset" rows read "set". Pin the knobs the suite assumes are
# at default; tests that exercise another mode set it explicitly.
_config.TITLES = "off"
_config.TITLE_ALLOW = ""
# The decision modes too: she now runs dodge/speak/stance LIVE (user's
# 2026-11-15 call), and the seam tests assert the boot defaults.
_config.JEV_JUDGE_DODGE = "shadow"
_config.JEV_DECIDE_SPEAK = "shadow"
_config.JEV_DECIDE_STANCE = "shadow"
