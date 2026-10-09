"""What the CLI and SDK send for publishing, weights overrides and KV-cache
edits -- the request is the contract with the API, so it is what is pinned."""
import pytest

import corerun.inference as inference


class _Client:
    def __init__(self):
        self.calls = []

    def post(self, path, json=None, workspace=None):
        self.calls.append(("POST", path, json))
        return {"model_name": "qwen38", "version": 1}

    def put(self, path, json=None, workspace=None):
        self.calls.append(("PUT", path, json))
        return {"id": "s1", "name": "q", "model_id": "m", "status": "deploying"}

    def get(self, path, params=None, workspace=None):
        self.calls.append(("GET", path, params))
        return {"available": True, "models": []}


@pytest.fixture
def client(monkeypatch):
    c = _Client()
    monkeypatch.setattr(inference, "get_client", lambda: c)
    return c


def test_publish_from_the_catalogue(client):
    inference.publish_from_catalogue("qwen3-8-27b-nvfp4", name="qwen38")
    body = {"catalogue": "qwen3-8-27b-nvfp4", "name": "qwen38"}
    assert client.calls == [("POST", "/registry/models/publish", body)]


def test_a_weights_override(client):
    inference.deploy(
        name="q", model_id="qwen38", model_source="registry", compute_name="gb10",
        weights_path="/data/models/q", dry_run=True,
    )
    assert client.calls[0][2]["weights_override"] == {"source": "path", "id": "/data/models/q"}
    with pytest.raises(ValueError):
        inference.deploy(
            name="q", model_id="m", compute_name="c", weights_path="/a", weights_hf="o/m",
        )


def test_update_replaces_or_clears_the_kv_cache(client):
    inference.update("s1", kv_cache={"memory_gb": 16})
    inference.update("s1", kv_cache={})
    assert [c[2] for c in client.calls] == [{"kv_cache": {"memory_gb": 16}}, {"kv_cache": {}}]


def test_local_models_asks_for_the_compute(client):
    inference.local_models("gb10dgx01")
    assert client.calls == [("GET", "/inference-servers/local-models", {"compute": "gb10dgx01"})]
