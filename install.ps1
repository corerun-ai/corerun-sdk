# Install the corerun CLI on Windows, and the corerun skills for the coding
# agents on this machine (Claude Code, opencode, pi, Codex, and anything
# reading ~/.agents). The PowerShell twin of install.sh.
#
# Every corerun platform serves this script with its own address and its own
# build of the CLI filled in below:
#
#   irm https://<console>/api/v1/cli/install.ps1 | iex
#
# Piped into iex a script takes no arguments, so its options are environment
# variables:
#   CORERUN_URL          sign in to this platform once installed (the address
#                        you open the console at); served by a platform, its own
#   CORERUN_NO_LOGIN     set to install without signing in
#   CORERUN_AGENTS       the agents to install the skills for, comma-separated:
#                        claude, opencode, pi, codex, agents
#   CORERUN_NO_SKILLS    set to install the CLI only
#   CORERUN_DEVICE_CODE  set to sign in with a code approved in any browser
#   CORERUN_SDK_SOURCE   what to install instead of the published CLI
#
# Nothing here needs an administrator. uv is installed first if it is not there
# already, and the CLI is installed as a uv tool, in an environment of its own.
# Written for Windows PowerShell 5.1, which every Windows has.

$ErrorActionPreference = 'Stop'

function Say([string]$Message) { Write-Host $Message -ForegroundColor Cyan }
# Thrown, not exited: run through iex, exit would close the person's window.
function Fail([string]$Message) { throw "install.ps1: $Message" }

# Filled in by the platform that serves this script: its address, and the CLI
# it serves, built from the same source as the platform itself. Empty here.
$PlatformUrl = ''
$PlatformCli = ''

# Otherwise the published CLI, as an archive so installing it does not need git.
$Source = $env:CORERUN_SDK_SOURCE
if (-not $Source) { $Source = $PlatformCli }
if (-not $Source) { $Source = 'https://github.com/corerun-ai/corerun-sdk/archive/refs/heads/main.zip' }
$Url = $env:CORERUN_URL
if (-not $Url) { $Url = $PlatformUrl }
if ($env:CORERUN_NO_LOGIN) { $Url = '' }
$Sep = [IO.Path]::PathSeparator

# uv. Its installer puts it on the user's PATH for later shells; this one is
# told directly.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say 'Installing uv'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = (Join-Path (Join-Path $HOME '.local') 'bin') + $Sep + $env:Path
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail 'uv did not install; see https://docs.astral.sh/uv/' }
}

Say 'Installing the corerun CLI'
# --refresh, or a second run reinstalls the archive uv cached the first time.
uv tool install --force --refresh --quiet $Source
if ($LASTEXITCODE -ne 0) { Fail 'uv could not install the corerun CLI' }
$Bin = (uv tool dir --bin | Out-String).Trim()
$env:Path = $Bin + $Sep + $env:Path
if (-not (Get-Command corerun -ErrorAction SilentlyContinue)) { Fail "corerun was installed into $Bin but cannot be run from there" }
# Later shells: uv adds the tools directory to the user's PATH if it is not.
uv tool update-shell *> $null

if (-not $env:CORERUN_NO_SKILLS) {
    Say 'Installing the corerun skills for your coding agents'
    $AgentArgs = @()
    if ($env:CORERUN_AGENTS) {
        foreach ($Agent in $env:CORERUN_AGENTS.Split(',')) {
            if ($Agent.Trim()) { $AgentArgs += @('--agent', $Agent.Trim()) }
        }
    }
    corerun skills install --force @AgentArgs
    if ($LASTEXITCODE -ne 0) { Fail 'the skills did not install' }
}

if ($Url) {
    Say "Signing in to $Url"
    $Login = @('login', '--url', $Url)
    # Over SSH there is no browser here to open: a code, approved elsewhere.
    if ($env:CORERUN_DEVICE_CODE -or $env:SSH_CONNECTION) { $Login += '--use-device-code' }
    corerun @Login
}

Write-Host ''
Say "$((corerun version | Select-Object -First 1)) is installed."
Write-Host 'Open a new terminal for corerun to be on PATH there.'
Write-Host ''
Write-Host 'Next:'
if (-not $Url) { Write-Host '  corerun login --url https://<your console address>' }
Write-Host '  corerun workspace list        # where you can work'
Write-Host '  corerun endpoints list        # models you can call'
Write-Host 'Then ask your coding agent to use corerun: the skills tell it how.'
