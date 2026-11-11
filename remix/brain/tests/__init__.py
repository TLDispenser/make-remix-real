# Test package. The suite must never touch the live brain's files, so the
# decision log is switched off here, once, before any test module loads
# (individual tests may re-enable it explicitly).
from brain import config as _config

_config.DECISIONS_LOG = False
