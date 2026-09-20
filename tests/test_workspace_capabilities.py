"""
The capability vocabulary the CLI accepts.

It is a hand-kept copy of models.AllCapabilities, which is fine until it
drifts — and it had. `experiments` and `traces` arrived with GenAI workspaces
and never reached this list, so the CLI rejected as "unknown capability" the
two keys the platform gates its entire GenAI surface on. Anybody creating a
GenAI workspace from the command line was told the thing they wanted did not
exist.
"""

from corerun.cli.workspace import CAPABILITIES

# What corerun/internal/models/models.go calls AllCapabilities. Repeated rather
# than imported, because it lives in the Go module and a test that reaches for
# it finds nothing and passes while checking nothing.
PLATFORM_CAPABILITIES = {
    "notebooks",
    "training",
    "models",
    "datasets",
    "images",
    "endpoints",
    "experiments",
    "traces",
}


def test_the_cli_knows_every_capability_the_platform_does():
    missing = PLATFORM_CAPABILITIES - set(CAPABILITIES)
    assert not missing, (
        f"the CLI would refuse {sorted(missing)} as unknown, though the platform "
        f"gates real routes on them"
    )


def test_the_cli_invents_none():
    extra = set(CAPABILITIES) - PLATFORM_CAPABILITIES
    assert not extra, (
        f"the CLI offers {sorted(extra)}, which the platform drops on normalisation "
        f"— accepted at the prompt and silently absent afterwards"
    )


def test_a_genai_workspace_can_be_described():
    """The set an onboarded GenAI workspace holds must be expressible."""
    for capability in ("experiments", "traces", "datasets", "endpoints"):
        assert capability in CAPABILITIES
