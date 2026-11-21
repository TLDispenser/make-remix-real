#!/usr/bin/env bash
# ptt.sh — push-to-talk control wrapper for your global hotkey (Phase 5).
# In your Hyprland config, bind one key's press and RELEASE (`bindr`):
#   bind  = SUPER, M, exec, /path/to/remix/ptt.sh start
#   bindr = SUPER, M, exec, /path/to/remix/ptt.sh stop
# (see godot/tools/hyprland-setup.md; the brain force-closes the mic after 15 s)
# Copy/symlink this file wherever the bind points (it reads standalone).
exec "$(dirname "$0")/.venv/bin/python" "$(dirname "$0")/brain/tools/ptt_ctl.py" "$@"
