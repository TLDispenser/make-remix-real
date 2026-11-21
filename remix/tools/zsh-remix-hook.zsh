# zsh-remix-hook — the Phase 6 shell hook (docs/history/Remix_Upgrade_Plan_V2.md, Part 4).
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
zmodload -F zsh/net/socket b:zsocket 2>/dev/null || true

_remix_preexec() {
    _REMIX_T0=$SECONDS
    _REMIX_CMD="$1"
}

_remix_send_sock() {
    local sock="$1" payload="$2"
    if (( $+builtins[zsocket] )); then
        local REPLY
        zsocket "$sock" 2>/dev/null || return 0
        print -r -u "$REPLY" -- "$payload" 2>/dev/null || true
        exec {REPLY}>&-
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(sys.argv[1]); s.sendall((sys.argv[2]+"\n").encode())' "$sock" "$payload" 2>/dev/null || true
    fi
}

_remix_precmd() {
    local rc=$?                    # capture BEFORE anything else runs
    local t0=$_REMIX_T0
    _REMIX_T0=""                   # one report per job, even if we skip it
    [[ -n "$t0" ]] || return
    local dur=$(( SECONDS - t0 ))
    (( dur >= 5 )) || return       # matches REMIX_SHELL_MIN_S default
    [[ -S "$_REMIX_SOCK" ]] || return
    local clean_cmd="${_REMIX_CMD//[$'\t\r\n']/ }"
    local payload="${clean_cmd}"$'\t'"${rc}"$'\t'"${dur}"
    _remix_send_sock "$_REMIX_SOCK" "$payload" &!
}

preexec_functions=(_remix_preexec $preexec_functions)
precmd_functions=(_remix_precmd $precmd_functions)
