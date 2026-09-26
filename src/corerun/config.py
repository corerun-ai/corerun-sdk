"""
Configuration management for corerun SDK.

Supports configuration via:
1. Direct initialization: corerun.init(auth_token="...", workspace="...")
2. Environment variables: CORERUN_AUTH_TOKEN, CORERUN_WORKSPACE, CORERUN_API_URL
3. Config file: ~/.corerun/config
4. Platform credential: /etc/corerun/api-key, installed in corerun notebooks

The platform credential is last so that anything the user chose themselves —
`corerun login`, an explicit key — wins over the one the notebook was handed.
"""

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from dotenv import load_dotenv

from corerun.credentials import credential_path, read_platform_credential



# Where a client looks unless it is told otherwise.
#
# Overridden by ~/.corerun/config or CORERUN_API_URL, which is what a
# self-hosted deployment does -- this is what corerun.ai serves, not an
# assumption that everybody uses it.
DEFAULT_API_URL = "https://corerun.ai/api/v1"

# Fixed at nothing: an address that suits one deployment is wrong for the next,
# which is how a CLI pointed at a development platform came to send its calls to
# a production host that no longer resolved. What is left is an override for the
# case the platform's own answer cannot cover, and otherwise the address is
# derived from the API address -- see inference_base_from below.
DEFAULT_INFERENCE_URL = ""

# What clients up to 0.1.0 wrote into the file on every login, because it was
# their default rather than anybody's decision. It is still in the files of
# everyone who signed in with one, where it reads as a choice and takes
# precedence over both the address the platform reports and the one its API
# address implies -- so a deployment that has since moved keeps being called at
# the address its clients used to look for. Recognised, it is treated as absent
# and the next save drops it.
LEGACY_INFERENCE_URL = "https://api.corerun.ai"


def _is_address(host: str) -> bool:
    """Whether a host is a literal address rather than a name."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def inference_base_from(api_url: str) -> str:
    """Where a deployment publishes its model endpoints, given its API address.

    A deployment's domain is ``corerun.ai`` for production and
    ``dev.corerun.ai`` for an environment that is named; the console is
    ``console`` under it and the gateway is ``api`` under it. The API is
    reached at either the domain itself or the console's host, and the
    gateway is ``api.corerun.ai`` or ``api.dev.corerun.ai`` from both. That is
    the address the deployment's own configuration reaches for, so it is what
    an override should start from.

    Empty when there is nothing to build from -- a bare name, a single label, a
    literal address -- because a guess at one of those names a host that was
    never published, which fails in a way the reader cannot tell from a
    deployment that is simply down.
    """
    parts = urlsplit(api_url or "")
    host = parts.hostname or ""
    labels = host.split(".")

    if len(labels) < 2 or _is_address(host):
        return ""

    scheme = parts.scheme or "https"

    # Already the gateway's own name: one host serving both, so there is no
    # second name to build -- and prefixing it again would invent one.
    if labels[0].startswith("api"):
        return f"{scheme}://{host}"

    # The console's host is the domain with a `console` label in front, and
    # the gateway is the same domain with `api` in front -- not `api` in front
    # of the console.
    if labels[0] == "console" and len(labels) >= 3:
        host = ".".join(labels[1:])

    return f"{scheme}://api.{host}"


@dataclass
class Config:
    """corerun configuration"""
    auth_token: Optional[str] = None

    # Exchanged for a new auth_token when the current one expires. Access tokens
    # are short-lived on purpose; without this, signing in would be a daily
    # chore rather than something done once.
    refresh_token: Optional[str] = None

    workspace: Optional[str] = None
    api_url: str = DEFAULT_API_URL

    # Where model endpoints are published, when something has to override what
    # the platform reports. Empty means nothing was chosen, which is not the
    # same as having nowhere to call: see inference_base.
    inference_url: str = DEFAULT_INFERENCE_URL
    timeout: int = 30
    verify_ssl: bool = True

    # Set when auth_token came from the platform credential file. The file is
    # rewritten while the notebook runs, so a long-lived client re-reads it
    # rather than trusting the value it started with.
    auth_token_file: Optional[Path] = None

    # Settings this process took from the environment rather than from the file.
    #
    # They are not written back. Exporting CORERUN_API_URL to point one command
    # at another deployment must not rewrite the file every later command reads
    # -- that turns a temporary override into a permanent one, and the symptom
    # shows up much later somewhere unrelated: a sign-in that opens the wrong
    # host, long after the export was forgotten.
    env_only: set = field(default_factory=set, repr=False, compare=False)

    @property
    def inference_base(self) -> str:
        """Where to call model endpoints: what was chosen, or what api_url implies.

        Kept apart from inference_url because the two are trusted differently.
        A chosen address is somebody's decision and overrides what the platform
        reports; a derived one is this SDK's guess about a deployment it can see
        the API address of, and stands only where the platform reports nothing.

        Derived rather than stored, and never written back: it follows api_url,
        so saving it would freeze the answer to a question that moves.
        """
        return self.inference_url or inference_base_from(self.api_url)

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables"""
        load_dotenv()
        verify_ssl_str = os.getenv("CORERUN_VERIFY_SSL", "true").lower()
        return cls(
            auth_token=os.getenv("CORERUN_AUTH_TOKEN"),
            workspace=os.getenv("CORERUN_WORKSPACE"),
            api_url=os.getenv("CORERUN_API_URL", DEFAULT_API_URL),
            inference_url=os.getenv("CORERUN_INFERENCE_URL", DEFAULT_INFERENCE_URL),
            timeout=int(os.getenv("CORERUN_TIMEOUT", "30")),
            verify_ssl=verify_ssl_str not in ("false", "0", "no"),
        )

    @classmethod
    def from_file(cls, path: Optional[Path] = None) -> "Config":
        """Load configuration from config file"""
        if path is None:
            path = Path.home() / ".corerun" / "config"

        if not path.exists():
            return cls()

        config = cls()
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip().lower()
                    value = value.strip().strip('"').strip("'")

                    if key == "auth_token":
                        config.auth_token = value
                    elif key == "refresh_token":
                        config.refresh_token = value
                    elif key == "workspace":
                        config.workspace = value
                    elif key == "api_url":
                        config.api_url = value
                    elif key == "inference_url":
                        # A value an older client wrote for itself is not a
                        # choice, and keeping it would pin this deployment to
                        # the address that client looked for. Any other value
                        # was put there by somebody, and stands.
                        if value != LEGACY_INFERENCE_URL:
                            config.inference_url = value
                    elif key == "timeout":
                        config.timeout = int(value)
                    elif key == "verify_ssl":
                        config.verify_ssl = value.lower() not in ("false", "0", "no")

        return config

    def save(self, path: Optional[Path] = None):
        """Save configuration to file.

        Written to a neighbouring file and moved into place, because the other
        half of this class reads the file whenever it wants a setting. Writing
        in place empties it for as long as the write takes, and a command that
        reads in that window finds no credentials at all -- "not logged in"
        right after signing in, for no reason the reader can see. A rename is
        atomic, so a reader sees the whole of whichever version it opens.
        """
        if path is None:
            path = Path.home() / ".corerun" / "config"

        path.parent.mkdir(parents=True, exist_ok=True)

        # Anything the environment supplied keeps whatever the file already
        # said, so saving preserves the setting rather than overwriting it.
        stored = Config.from_file(path) if path.exists() else Config()
        api_url = stored.api_url if "api_url" in self.env_only else self.api_url
        inference_url = stored.inference_url if "inference_url" in self.env_only else self.inference_url
        timeout = stored.timeout if "timeout" in self.env_only else self.timeout
        verify_ssl = stored.verify_ssl if "verify_ssl" in self.env_only else self.verify_ssl
        auth_token = stored.auth_token if "auth_token" in self.env_only else self.auth_token
        workspace = stored.workspace if "workspace" in self.env_only else self.workspace

        pending = path.with_name(path.name + ".new")
        with open(pending, "w") as f:
            if auth_token:
                f.write(f"auth_token={auth_token}\n")
            if self.refresh_token:
                f.write(f"refresh_token={self.refresh_token}\n")
            if workspace:
                f.write(f"workspace={workspace}\n")
            f.write(f"api_url={api_url}\n")
            if inference_url:
                f.write(f"inference_url={inference_url}\n")
            f.write(f"timeout={timeout}\n")
            if not verify_ssl:
                f.write("verify_ssl=false\n")

        os.replace(pending, path)


def _apply_platform_credential(config: "Config") -> None:
    """
    Fall back to the credential a corerun notebook was launched with.

    Only when nothing else supplied one, so a user who signed in keeps their own
    identity. The path is remembered either way, so the client can pick up a
    renewed key without being rebuilt.
    """
    if config.auth_token:
        return
    key = read_platform_credential()
    if key:
        config.auth_token = key
        config.auth_token_file = credential_path()


# Global configuration
_config: Optional[Config] = None
_client: Optional["CoreRunClient"] = None


def init(
    auth_token: Optional[str] = None,
    workspace: Optional[str] = None,
    api_url: Optional[str] = None,
    timeout: Optional[int] = None,
) -> "CoreRunClient":
    """
    Initialize the corerun SDK.

    Args:
        auth_token: corerun auth token (or set CORERUN_AUTH_TOKEN env var)
        workspace: Default workspace ID (or set CORERUN_WORKSPACE env var)
        api_url: API base URL (default: https://corerun.ai/api/v1)
        timeout: Request timeout in seconds (default: 30)

    Returns:
        CoreRunClient instance

    Example:
        import corerun
        corerun.init(auth_token="cr-xxx", workspace="my-workspace-id")
    """
    global _config, _client

    # Load from file first, then overlay env vars
    _config = Config.from_file()
    env_config = Config.from_env()
    if env_config.auth_token:
        _config.auth_token = env_config.auth_token
    if env_config.workspace:
        _config.workspace = env_config.workspace
    if os.getenv("CORERUN_API_URL"):
        _config.api_url = env_config.api_url
    if os.getenv("CORERUN_INFERENCE_URL"):
        _config.inference_url = env_config.inference_url
    if os.getenv("CORERUN_TIMEOUT"):
        _config.timeout = env_config.timeout
    # Read from the environment like everything else above it. Parsed in
    # from_env since it was written and dropped here, so CORERUN_VERIFY_SSL did
    # nothing at all unless it was also in the config file -- which meant every
    # development cluster with a self-signed certificate was unreachable with no
    # way to say otherwise.
    if os.getenv("CORERUN_VERIFY_SSL"):
        _config.verify_ssl = env_config.verify_ssl
    _apply_platform_credential(_config)

    # Override with explicit arguments
    if auth_token:
        _config.auth_token = auth_token
    if workspace:
        _config.workspace = workspace
    if api_url:
        _config.api_url = api_url
    if timeout:
        _config.timeout = timeout

    # Validate
    if not _config.auth_token:
        raise ValueError(
            "auth token required. Run `corerun login`, set the CORERUN_AUTH_TOKEN "
            "environment variable, or pass auth_token to corerun.init()"
        )

    # Create client
    from corerun.client import CoreRunClient
    _client = CoreRunClient(_config)

    return _client


def get_config() -> Config:
    """Get current configuration"""
    global _config
    if _config is None:
        # Start with file config (from ~/.corerun/config), then overlay env vars
        _config = Config.from_file()
        env_config = Config.from_env()
        if env_config.auth_token:
            _config.auth_token = env_config.auth_token
            _config.env_only.add("auth_token")
        if env_config.workspace:
            _config.workspace = env_config.workspace
            _config.env_only.add("workspace")
        if os.getenv("CORERUN_API_URL"):
            _config.api_url = env_config.api_url
            _config.env_only.add("api_url")
        if os.getenv("CORERUN_INFERENCE_URL"):
            _config.inference_url = env_config.inference_url
            _config.env_only.add("inference_url")
        if os.getenv("CORERUN_TIMEOUT"):
            _config.timeout = env_config.timeout
            _config.env_only.add("timeout")
        if os.getenv("CORERUN_VERIFY_SSL"):
            _config.verify_ssl = env_config.verify_ssl
            _config.env_only.add("verify_ssl")
        _apply_platform_credential(_config)
    return _config


def get_client() -> "CoreRunClient":
    """
    Get the current corerun client.

    Raises ValueError if not initialized.
    """
    global _client
    if _client is None:
        # Try auto-init from environment
        config = get_config()
        if config.auth_token:
            return init()
        raise ValueError(
            "corerun SDK not initialized. Call corerun.init() first "
            "or set CORERUN_AUTH_TOKEN environment variable."
        )
    return _client
