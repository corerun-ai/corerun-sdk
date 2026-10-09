"""Keeping a model's KV cache beyond the GPU, as the SDK asks for it.

What these pin is the request: each tier only when it was asked for, both
together when both were, and nothing at all for the GPU alone.
"""
import corerun.inference as inference


class _Client:
    def __init__(self):
        self.posted = []

    def post(self, path, json=None, workspace=None):
        self.posted.append((path, json))
        return {"would_refuse": []}


def _deploy(monkeypatch, **kwargs):
    client = _Client()
    monkeypatch.setattr(inference, "get_client", lambda: client)
    inference.deploy(
        name="qwen", model_id="unsloth/Qwen3.8-27B-NVFP4", compute_name="gb10dgx01",
        dry_run=True, **kwargs,
    )
    return client.posted[0][1]


def test_the_disk_tier_sends_its_folder_and_budget(monkeypatch):
    body = _deploy(
        monkeypatch, kv_cache_disk="/var/lib/corerun/kvcache", kv_cache_disk_budget_gb=400,
    )
    assert body["kv_cache"] == {"disk": {"path": "/var/lib/corerun/kvcache", "budget_gb": 400}}


def test_both_tiers_together(monkeypatch):
    body = _deploy(
        monkeypatch, kv_cache_memory_gb=32, kv_cache_disk="/data/kv", kv_cache_disk_budget_gb=100,
    )
    assert body["kv_cache"] == {"memory_gb": 32, "disk": {"path": "/data/kv", "budget_gb": 100}}


def test_host_memory_alone(monkeypatch):
    assert _deploy(monkeypatch, kv_cache_memory_gb=16)["kv_cache"] == {"memory_gb": 16}


def test_the_gpu_alone_sends_nothing(monkeypatch):
    assert "kv_cache" not in _deploy(monkeypatch)


def test_an_edit_names_what_it_replaces(monkeypatch):
    assert _deploy(monkeypatch, replaces="b5f4db9e")["replaces"] == "b5f4db9e"
    assert "replaces" not in _deploy(monkeypatch)
