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

## Push-to-talk
Hold-to-talk = two binds on the same key: press starts the mic, **release** stops it
and transcribes. Both just run `remix/ptt.sh start|stop`, which pokes the brain.
In hyprlang a *release* bind is `bindr` (the `r` flag), NOT an `up` argument:

    bind  = SUPER, M, exec, /path/to/remix/ptt.sh start
    bindr = SUPER, M, exec, /path/to/remix/ptt.sh stop

Check the wiki (https://wiki.hypr.land/Configuring/Binds/) for your version's syntax
(Lua configs differ). If you bind a modifier combo and let go of the modifier before the
letter, some versions skip the release: the brain closes the mic by itself after
`REMIX_PTT_MAX_PRESS_S` (15 s), so it can never stay open. No compositor setup yet? Focus
her window and hold **F2** instead, or type `/talk` in `./talk.sh`.

## Things not verified on real Hyprland (could not test here)
Click-drag via the compositor, scroll-wheel resizing under a floating rule, and how
your compositor treats her size hints (she is resizable and fits any size she is given).
There are no rounded corners and no transparency any more, so nothing depends on your
compositor having a transparency path.
