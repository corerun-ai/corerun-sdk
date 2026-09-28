"""The engine version a server shows: what the engine reported once running,
not only what the catalogue claimed when it was deployed.

The two differ whenever an image tag moved under the catalogue, and the
running one is the one that decides which flags the engine accepts.
"""
from datetime import datetime, timezone

import corerun.cli.inference as inf_cli
import corerun.inference as inference

_BASE = {
    "id": "srv-1",
    "name": "qwen",
    "model_id": "Qwen/Qwen3-8B",
    "compute_name": "gpu-small",
    "image": "vllm/vllm-openai:v0.11.0",
    "owner_id": "u1",
    "engine_version": "0.11.0",
}


def test_the_running_version_is_parsed():
    s = inference._server_from_response(
        {**_BASE, "running_engine_version": "0.11.2", "running_engine_version_at": "2026-09-27T08:15:00Z"}
    )

    assert s.running_engine_version == "0.11.2"
    assert s.running_engine_version_at == datetime(2026, 9, 27, 8, 15, tzinfo=timezone.utc)
    # The catalogue's claim is kept beside it, not overwritten.
    assert s.engine_version == "0.11.0"


def test_a_server_not_yet_seen_running_has_no_running_version():
    s = inference._server_from_response(dict(_BASE))

    assert s.running_engine_version is None
    assert s.running_engine_version_at is None


def test_get_shows_the_running_version_and_when_it_was_seen():
    s = inference._server_from_response(
        {**_BASE, "running_engine_version": "0.11.2", "running_engine_version_at": "2026-09-27T08:15:00Z"}
    )

    label = inf_cli._engine_label(s)

    assert "engine 0.11.2, running, seen 2026-09-27 08:15" in label
    assert "catalogue said 0.11.0" in label


def test_get_falls_back_to_the_catalogue_version():
    s = inference._server_from_response(dict(_BASE))

    label = inf_cli._engine_label(s)

    assert "engine 0.11.0" in label
    assert "running" not in label


def test_no_version_at_all_says_nothing():
    s = inference._server_from_response({**_BASE, "engine_version": None})

    assert inf_cli._engine_label(s) == ""
