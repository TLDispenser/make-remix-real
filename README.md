# Remix

**Remix** is a desktop companion with a two-brain architecture:

- **Body (`remix/godot/`):** Godot 4.7 window rendering an SVG-driven ribbon face, CRT styling, audio-synced mouth & eye acting cues, and text/push-to-talk input over WebSocket protocol `v2`.
- **Brain (`remix/brain/`):** Python daemon handling local vitals (`psutil`), local awareness & privacy redaction (Hyprland), persistent dated journal & SQLite FTS5 memory, System-1 decisions (local `RuleBrain` + optional Jev), System-2 speech via `$0` LLM providers, local push-to-talk STT (`faster-whisper`), and non-AI voice synthesis (`espeak-ng` + optional DSP).

## Quickstart

```bash
cd remix
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # add API keys if desired; runs locally without them

# Run both brain + face:
./restart.sh

# Or chat from a terminal (starts the brain if needed):
./talk.sh

# Configure knobs in the interactive TUI or plain menu:
./settings.sh
```

## Repository layout

```text
.
├── docs/                   # Living design & API notes
│   ├── NEW_SPEC.md
│   ├── RESEARCH_NOTES.md
│   ├── HERMETIC_RULES.md
│   └── history/            # Archived planning & migration docs
└── remix/
    ├── brain/              # Python daemon, modules, CLI tools, unit tests
    ├── godot/              # Godot 4.7 face project, art assets, headless tests
    ├── tools/              # Zsh hook and setup helpers
    ├── pyproject.toml
    ├── requirements.txt
    ├── restart.sh
    ├── talk.sh
    ├── ptt.sh
    └── settings.sh
```

## Documentation

- [Runtime & setup guide](remix/README.md)
- [Specification](docs/NEW_SPEC.md)
- [API & platform research notes](docs/RESEARCH_NOTES.md)
- [Godot face README](remix/godot/README.md) and [Protocol v2 contract](remix/godot/PROTOCOL.md)
- [Hermetic test rules](docs/HERMETIC_RULES.md)

## Running tests

```bash
# Python unit suite (hermetic, offline)
cd remix
python3 -m unittest discover -s brain/tests -t .

# Godot headless suite (requires Godot 4.7+)
cd remix/godot
godot --headless --path . --script res://tests/shapes_audit.gd
godot --headless --path . --script res://tests/engine_test.gd
godot --headless --path . --script res://tests/perform_test.gd
godot --headless --path . --script res://tests/talk_test.gd
```
