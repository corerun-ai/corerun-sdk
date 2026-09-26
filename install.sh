#!/bin/sh
# Install the corerun CLI, and the corerun skills for the coding agents on this
# machine (Claude Code, opencode, pi, Codex, and anything reading ~/.agents).
#
# Every corerun platform serves this script with its own address and its own
# build of the CLI filled in below, so the one line its Home page shows is all
# it takes:
#
#   curl -fsSL https://<console>/api/v1/cli/install.sh | sh
#
# Run from a copy of the repository, it installs the published CLI and signs
# in wherever --url says.
#
# Options:
#   --url URL        sign in to this platform once installed (the address you
#                    open the console at); served by a platform, its own
#   --no-login       install, and do not sign in
#   --agent NAME     install the skills for this agent only: claude, opencode,
#                    pi, codex or agents; repeat for several
#   --no-skills      install the CLI only
#   --device-code    sign in with a code shown here and approved in any
#                    browser; the default on a machine with no display, such
#                    as a server reached over SSH
#
# Environment:
#   CORERUN_SDK_SOURCE  what to install instead of the published CLI -- an
#                       archive URL, a git URL (git+https://...@branch) or a
#                       local path
#
# Nothing here needs root. The CLI is installed with uv as a tool of its own,
# in its own environment, so it cannot disturb the Python a project uses; uv is
# installed first, into ~/.local/bin, if it is not there already.
set -eu

# Filled in by the platform that serves this script: its address, and the CLI
# it serves, built from the same source as the platform itself. Empty here.
PLATFORM_URL=""
PLATFORM_CLI=""

# Otherwise the published CLI, as an archive rather than a git URL so that
# installing it does not need git.
SOURCE="${CORERUN_SDK_SOURCE:-${PLATFORM_CLI:-https://github.com/corerun-ai/corerun-sdk/archive/refs/heads/main.zip}}"
# What a new shell will have, before this script adds to it.
ORIGINAL_PATH="$PATH"
URL="$PLATFORM_URL"
AGENTS=""
SKILLS=1
DEVICE_CODE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --url) URL="${2:?--url needs an address}"; shift 2 ;;
        --url=*) URL="${1#--url=}"; shift ;;
        --agent) AGENTS="$AGENTS --agent ${2:?--agent needs a name}"; shift 2 ;;
        --agent=*) AGENTS="$AGENTS --agent ${1#--agent=}"; shift ;;
        --no-skills) SKILLS=0; shift ;;
        --no-login) URL=""; shift ;;
        --device-code) DEVICE_CODE=1; shift ;;
        -h|--help) sed -n '2,22p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1m%s\033[0m\n' "$*"; }
fail() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || command -v uv >/dev/null 2>&1 || fail "curl is needed to install uv"
command -v git >/dev/null 2>&1 || case "$SOURCE" in git+*) fail "git is needed to install the CLI from $SOURCE" ;; esac

# uv, where the shell will find it now and later.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
    command -v uv >/dev/null 2>&1 || fail "uv did not install; see https://docs.astral.sh/uv/"
fi

say "Installing the corerun CLI"
# --refresh, or a second run reinstalls the archive uv cached the first time.
uv tool install --force --refresh --quiet "$SOURCE"
BIN="$(uv tool dir --bin)"
export PATH="$BIN:$PATH"
command -v corerun >/dev/null 2>&1 || fail "corerun was installed into $BIN but cannot be run from there"

if [ "$SKILLS" = 1 ]; then
    say "Installing the corerun skills for your coding agents"
    # shellcheck disable=SC2086 # AGENTS is a list of flags
    corerun skills install --force $AGENTS
fi

if [ -n "$URL" ]; then
    say "Signing in to $URL"
    # Piped into sh, standard input is this script: sign-in reads the
    # terminal instead, where the person running it is.
    # Opened to find out: /dev/tty can exist and be readable with no terminal
    # behind it, as in CI or a container.
    #
    # With no display to open a browser on -- over SSH, or a Linux box with
    # no desktop -- sign-in shows a code to approve from any other device.
    if [ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ] ||
        { [ "$(uname -s)" = Linux ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; }; then
        DEVICE_CODE=1
    fi
    if (: </dev/tty) 2>/dev/null; then
        if [ "$DEVICE_CODE" = 1 ]; then
            corerun login --url "$URL" --use-device-code </dev/tty
        else
            corerun login --url "$URL" </dev/tty
        fi
    else
        echo "No terminal to sign in from. Run:  corerun login --url $URL --use-device-code"
    fi
fi

# A new shell finds the CLI only if its directory is on PATH there too; uv
# adds it to the shell's profile when it is not.
case ":$ORIGINAL_PATH:" in
    *":$BIN:"*) ;;
    *)
        # Asked with the PATH a new shell will have: with this script's, uv
        # sees the directory already there and changes nothing.
        # uv also needs to know the shell; SHELL is unset in some
        # containers, and the account's login shell is the answer then.
        LOGIN_SHELL="${SHELL:-$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7)}"
        env PATH="$ORIGINAL_PATH" SHELL="${LOGIN_SHELL:-/bin/sh}" "$(command -v uv)" tool update-shell >/dev/null 2>&1 || true
        NOTE="Open a new terminal, or run:  export PATH=\"$BIN:\$PATH\""
        ;;
esac

echo
say "$(corerun version 2>/dev/null | head -1 || echo corerun) is installed."
[ -n "${NOTE:-}" ] && echo "$NOTE"
echo
echo "Next:"
[ -z "$URL" ] && echo "  corerun login --url https://<your console address>     # --use-device-code on a server"
echo "  corerun workspace list        # where you can work"
echo "  corerun endpoints list        # models you can call"
echo "Then ask your coding agent to use corerun: the skills tell it how."
