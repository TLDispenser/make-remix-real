#!/usr/bin/env bash
# One-time permission setup for Phase 5 push-to-talk (Hyprland).
# NOT RUN AUTOMATICALLY. Two options lived in Phase 0 research; this file
# only documents the decision points. Nothing here is wired yet.
#
# 1) PTT via Hyprland binds (recommended, permission-light):
#    bind = <mod>, <KEY>, exec, <core>/core/brain/tools/ptt-client.sh press
#    bindr = <mod>, <KEY>, exec, <core>/core/brain/tools/ptt-client.sh release
#    (press starts mic capture, release stops + transcribes; wired in Phase 5)
#
# 2) Always-on-top on Wayland (Godot doesn't implement it; Hyprland does):
#    windowrulev2 = pin on, float on, class:^(godot)$
#    -- verify the real class first: hyprctl clients | grep -i class
