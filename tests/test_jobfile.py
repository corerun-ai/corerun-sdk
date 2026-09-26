"""A job file in SkyPilot's shape becomes the job it describes."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from corerun import jobfile


def test_a_skypilot_style_file_maps_onto_the_job(tmp_path):
    (tmp_path / "code").mkdir()
    f = tmp_path / "corerun.yaml"
    f.write_text(
        "name: vision-train\n"
        "workdir: code\n"
        "image: pytorch/pytorch:2.4.0\n"
        "resources: {compute: gke, profile: gpu-h100-1, priority: high, max_runtime: 6h}\n"
        "envs: {EPOCHS: 10}\n"
        "datasets: [imagenet]\n"
        "setup: pip install -e .\n"
        "run: python train.py --epochs $EPOCHS\n"
    )
    spec = jobfile.load(f)
    assert spec["name"] == "vision-train" and spec["compute_name"] == "gke" and spec["profile"] == "gpu-h100-1"
    assert spec["priority"] == "high" and spec["max_runtime_minutes"] == 360
    assert spec["environment"] == {"EPOCHS": "10"} and spec["datasets"] == ["imagenet"]
    assert spec["source_directory"] == (tmp_path / "code").resolve()
    assert spec["command"] == ["sh", "-c", "pip install -e .\npython train.py --epochs $EPOCHS"]
    # setup replaces the automatic requirements install
    assert spec["install_requirements"] is False


def test_code_from_a_repository_and_mistakes_said_plainly(tmp_path):
    f = tmp_path / "corerun.yaml"
    f.write_text("run: python train.py\ngit: {url: acme/trainer, connection: github, ref: v2}\n")
    spec = jobfile.load(f)
    assert spec["git_url"] == "acme/trainer" and spec["connection"] == "github" and spec["ref"] == "v2"
    assert "install_requirements" not in spec  # the default applies

    for body, says in [
        ("run: x\nworkdir: .\nrepo: train\n", "one of workdir, repo or git"),
        ("run: x\ncommand: y\n", "unknown keys"),
        ("setup: pip install x\n", "setup is given but run is not"),
        ("run: x\nresources: {max_runtime: soon}\n", "max_runtime"),
    ]:
        f.write_text(body)
        with pytest.raises(jobfile.JobFileError, match=says):
            jobfile.load(f)


def test_the_command_after_double_dash_is_kept_exactly(monkeypatch):
    from corerun.cli import app
    import corerun.jobs as jobs

    seen = {}

    def fake_submit(**kwargs):
        seen.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr("corerun.cli.jobs._init_client", lambda: None)
    monkeypatch.setattr(jobs, "submit", fake_submit)
    CliRunner().invoke(app, ["jobs", "submit", "--name", "t", "--image", "i", "--compute", "c",
                             "--", "python", "train.py", "--lr", "1e-4", "--note", "a b"])
    assert seen["command"] == ["python", "train.py", "--lr", "1e-4", "--note", "a b"]
