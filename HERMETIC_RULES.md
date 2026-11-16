# Hermetic rules — how to work near Remix without touching her life

Her brain is not a dev server. It runs 24/7 against her real memory
(`~/.local/share/remix/sandbox/`), and everything typed into it becomes
part of her permanent record. Two violations have already happened
(SyncSafetyTests leaked into her store on 11-14; a live smoke test typed
the fern/Kelvin-adjacent "site color" exchange into her journal on
11-11 22:49). Rules, learned the hard way:

1. **Never smoke-test against the live brain.** `ws://127.0.0.1:8765`
   and `talk.sh` are HER — a message sent there is a real memory she
   keeps. There is no "test message"; there is only residue to wipe.

2. **Need a live-ish brain? Spawn a disposable one.** `config.SANDBOX_DIR`
   is env-overridable:
   `SANDBOX_DIR=/tmp/opencode/remix-test-$$ WS_PORT=8766 ./.venv/bin/python -u -m brain.main`
   Test against that; delete the dir after. Same for face work:
   `godot/tools/fake_brain.py` exists precisely so Godot never needs her.

3. **Unit tests never touch her sandbox.** Suites redirect via
   `fresh_sandbox` (tests/__init__.py); any new test class MUST use it.
   tearDown must deactivate memory (`memory._active = False`) — that
   was leak #1 — and must close every event loop it opens.

4. **Slash commands are not read-only.** `/memory`, `/forget`,
   `/decisions` all mutate state. Typing one "just to check" through
   the live port is a mutation of her record.

If residue ever does land in her sandbox: wipe via `/forget <topic>`
(keeps store/DB/FTS/said-set/git consistent), delete orphan rows by id,
scrub the journal/diary renders, commit the sandbox mirror, and let the
diary migration re-author affected pages (delete `.voice-migrated`).
