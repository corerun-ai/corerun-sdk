"""
corerun CLI

Command-line interface for corerun ML Platform.

Usage:
    corerun login
    corerun datasets list
    corerun datasets import huggingface ylecun/mnist --name mnist
    corerun jobs submit --name train --image pytorch/pytorch:latest --gpu 1
    corerun jobs logs <job-id>
    corerun models list
    corerun models push my-model ./checkpoint -f pytorch
    corerun models pull my-model@2
    corerun models stage my-model 1 production
    corerun models alias my-model champion 1
    corerun genai traces list -e <experiment> --state ERROR
    corerun genai sessions list -e <experiment>
    corerun finetune list
    corerun finetune create --name my-ft \\
        --model meta-llama/Llama-3.2-3B-Instruct --dataset <id> --compute dgx
    corerun finetune wait <job-id>
    corerun inference list
    corerun inference deploy --name mistral --model mistralai/Mistral-7B --compute dgx --gpu 1
    corerun inference scale <server-id> --min-replicas 2 --max-replicas 4
    corerun clusters list
    corerun clusters get gb10dgx01
    corerun cluster add gpu1 --accelerator-family h100
    corerun clusters token rotate gpu1
    corerun host add dgx1
    corerun compute list
    corerun quota show
"""

import typer
from rich.console import Console

from corerun.cli import output
from corerun.cli.accelerators import app as accelerators_app
from corerun.cli.org import app as org_app
from corerun.cli.prices import app as prices_app
from corerun.cli.auth import app as auth_app
from corerun.cli.catalogue import app as catalogue_app
from corerun.cli.clusters import app as clusters_app
from corerun.cli.compute import app as compute_app
from corerun.cli.datasets import app as datasets_app
from corerun.cli.endpoints import app as endpoints_app
from corerun.cli.finetune import app as finetune_app
from corerun.cli.genai import app as genai_app
from corerun.cli.groups import app as groups_app
from corerun.cli.hosts import app as hosts_app
from corerun.cli.inference import app as inference_app
from corerun.cli.jobs import app as jobs_app
from corerun.cli.notebooks import app as notebooks_app
from corerun.cli.quota import app as quota_app
from corerun.cli.registry import app as registry_app
from corerun.cli.repos import app as repos_app
from corerun.cli.skills import app as skills_app
from corerun.cli.storage import app as storage_app
from corerun.cli.tokens import app as tokens_app
from corerun.cli.workspace import app as workspace_app

console = Console()

app = typer.Typer(
    name="corerun",
    help="corerun ML Platform CLI",
    no_args_is_help=True,
    add_completion=True,
)


def run() -> None:
    """The entry point: the commands, and one sentence for any failure.

    A command that fails raises a CoreRunError -- the SDK client and the
    CLI's own request helper both do -- and this turns it into the line the
    user reads and an exit status a script can test. Without it Typer printed
    a traceback, and before that a decode error named nothing that happened.
    """
    import json as _json

    from corerun.exceptions import CoreRunError
    from corerun.http import RETRY_HINT

    try:
        app()
    except CoreRunError as e:
        output.fail(str(e))
        raise SystemExit(1)
    except _json.JSONDecodeError as e:
        output.fail(f"The platform's answer was not JSON ({e}). {RETRY_HINT}")
        raise SystemExit(1)


@app.callback()
def main(
    json_output: bool = typer.Option(
        False, "--json", help="Print results as JSON, for piping into other tools"
    ),
):
    """corerun ML Platform CLI."""
    # Set before any command runs, so a command need only hand its result to
    # output.emit and never branch on the mode itself.
    output.set_json(json_output)

# Add subcommands
app.add_typer(
    accelerators_app,
    name="accelerators",
    help="Accelerator generations, and what this platform has been told",
)
app.add_typer(auth_app, name="auth", help="Authentication commands")
app.add_typer(datasets_app, name="datasets", help="Dataset management")
app.add_typer(jobs_app, name="jobs", help="Job management")
app.add_typer(repos_app, name="repos", help="Code repositories for training jobs")
app.add_typer(notebooks_app, name="notebooks", help="Notebook sessions")
app.add_typer(registry_app, name="models", help="Model registry")
app.add_typer(skills_app, name="skills", help="corerun skills for coding agents")
app.add_typer(finetune_app, name="finetune", help="Fine-tuning jobs")
app.add_typer(genai_app, name="genai", help="Agent traces, sessions and retention")
app.add_typer(tokens_app, name="tokens", help="Tokens for exporters, CI and scripts")
app.add_typer(inference_app, name="inference", help="Inference servers")
app.add_typer(endpoints_app, name="endpoints", help="Model endpoints")
app.add_typer(catalogue_app, name="catalogue", help="Models available from the catalogue")
app.add_typer(catalogue_app, name="catalog", help="Alias for catalogue")
app.add_typer(clusters_app, name="clusters", help="Clusters, and adding one")
app.add_typer(hosts_app, name="hosts", help="Bare-metal hosts")
app.add_typer(compute_app, name="compute", help="Compute targets")
app.add_typer(quota_app, name="quota", help="Workspace quota")
app.add_typer(storage_app, name="storage", help="Storage accounts")
app.add_typer(workspace_app, name="workspace", help="Workspace selection")
app.add_typer(groups_app, name="groups", help="Groups, and the roles they hold")
app.add_typer(org_app, name="org", help="Your organisation: people, sign-in, service accounts, licence, limits, usage, security and prices")
app.add_typer(prices_app, name="prices", help="Providers, and what their models cost")

# Aliases for convenience
app.add_typer(datasets_app, name="data", hidden=True)
app.add_typer(datasets_app, name="ds", hidden=True)
app.add_typer(registry_app, name="registry", hidden=True)
app.add_typer(repos_app, name="repo", hidden=True)
# Singular reads better for the commands that act on one model ("model pull"),
# and is what people type; both names reach the same commands.
app.add_typer(registry_app, name="model", hidden=True)
app.add_typer(finetune_app, name="ft", hidden=True)
app.add_typer(inference_app, name="serve", hidden=True)
app.add_typer(clusters_app, name="cluster", hidden=True)
# `host` is what people type and what the docs say; the group is plural
# because every other group with several commands is.
app.add_typer(hosts_app, name="host", hidden=True)
# "ws" is what gets typed; both reach the same commands.
app.add_typer(workspace_app, name="ws", hidden=True)
app.add_typer(groups_app, name="group", hidden=True)


@app.command()
def login(
    auth_token: str = typer.Option(None, "--token", "-t", help="Auth token"),
    workspace: str = typer.Option(None, "--workspace", "-w", help="Default workspace ID"),
    api_url: str = typer.Option(None, "--url", help="API URL"),
    use_device_code: bool = typer.Option(
        False,
        "--use-device-code",
        help="Show a code to approve elsewhere instead of opening a browser",
    ),
):
    """
    Login to corerun (alias for 'corerun auth login')
    """
    from corerun.cli.auth import login as auth_login
    auth_login(
        auth_token=auth_token,
        workspace=workspace,
        api_url=api_url,
        use_device_code=use_device_code,
    )


@app.command()
def whoami():
    """
    Show current user info (alias for 'corerun auth whoami')
    """
    from corerun.cli.auth import whoami as auth_whoami
    auth_whoami()


@app.command()
def logout():
    """
    Sign out (alias for 'corerun auth logout')
    """
    from corerun.cli.auth import logout as auth_logout
    auth_logout()


@app.command()
def version():
    """
    Show version information
    """
    from corerun import __version__
    console.print(f"corerun version {__version__}")


if __name__ == "__main__":
    app()
