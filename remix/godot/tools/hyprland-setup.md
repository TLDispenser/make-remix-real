# Remix on Hyprland (Wayland)

Wayland apps cannot pin or float themselves; Hyprland does it through window rules.
Remix's window title is always exactly `Remix`, so match on that.

**Hyprland's rule syntax has changed recently** (hyprlang is deprecated for Lua since
0.55, and the rule names changed in 0.53+). Check which format YOUR config uses and
read the wiki page for your version before copying anything:
https://wiki.hypr.land/Configuring/Basics/Window-Rules/

What you want, in words: **float** her, **pin** her (all workspaces; pin only works on
floating windows), and **don't steal focus when she opens** (`no_initial_focus`).
Do NOT use `no_focus`: she needs focus for click-drag and the scroll-wheel resize.
Optional: `size`/`move` rules to pick her size and corner.

Shapes the wiki showed for recent versions (verify, don't trust):

    # Lua
    hl.window_rule({ name = "remix", match = { title = "^Remix$" },
                     float = true, pin = true, no_initial_focus = true })

    # new hyprlang style
    windowrule = match:title ^Remix$, float on, pin on, no_initial_focus on

Find the real title/class if it doesn't match:  `hyprctl clients | grep -iA3 remix`

## Push-to-talk (later phase)
Use compositor binds (press = start mic, release = stop + transcribe) that call a
small script which messages the Python brain, instead of reading raw input devices.
Keep the bind syntax from the wiki for your version.

## Things not verified on real Hyprland (could not test here)
Per-pixel transparency of the rounded glass corners, click-drag via the compositor,
and scroll-wheel resizing under a floating rule. If corners show black, your setup
has no transparency available; the face is unaffected.
