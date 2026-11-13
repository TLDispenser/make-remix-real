# zsh-remix-hook — the Phase 6 shell hook (Remix_Upgrade_Plan_V2.md, Part 4).
#
# She IS the laptop, so she notices what you do in the shell — but only the
# things worth noticing: commands that ran >= 5 seconds (builds, jobs, long
# runs) and how they ended. Short commands are life, not news.
#
# Install:
#   1. Set REMIX_SHELL_HOOK=1 in remix/.env (off by default: a command line
#      is user content and her reaction about it goes to the cloud LLM —
#      the brain redacts emails/URLs/paths and caps the length first, but
#      enable only if you're happy with that).
#   2. Source this file from ~/.zshrc:
#        source /path/to/remix/tools/zsh-remix-hook.zsh
#   3. Restart the brain (./restart.sh brain) so the socket is listening.
#
# The socket path below must match REMIX_SHELL_SOCK in remix/.env if you
# changed it there.

typeset -g _REMIX_SOCK="${XDG_RUNTIME_DIR:-/run/user/$UID}/remix/shell.sock"
typeset -g _REMIX_T0="" _REMIX_CMD=""

_remix_preexec() {
    _REMIX_T0=$SECONDS
    _REMIX_CMD="$1"
}

_remix_precmd() {
    local rc=$?                    # capture BEFORE anything else runs
    local t0=$_REMIX_T0
    _REMIX_T0=""                   # one report per job, even if we skip it
    [[ -n "$t0" ]] || return
    local dur=$(( SECONDS - t0 ))
    (( dur >= 5 )) || return       # matches REMIX_SHELL_MIN_S default
    [[ -S "$_REMIX_SOCK" ]] || return
    printf '%s\t%s\t%s\n' "$_REMIX_CMD" "$rc" "$dur" > "$_REMIX_SOCK" 2>/dev/null &
}

preexec_functions=(_remix_preexec $preexec_functions)
precmd_functions=(_remix_precmd $precmd_functions)
