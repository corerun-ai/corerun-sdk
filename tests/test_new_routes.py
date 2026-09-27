"""The routes the API served and nothing here called: dataset upload and
metadata, an inference server's key, a fine-tune's sizing, and the catalogue's
compatibility check. What each sends, and what the CLI makes of the answer.
"""

import io
import json
import tarfile

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import datasets, finetune, inference
from corerun.cli import app
from corerun.cli import catalogue as catalogue_cli
from corerun.cli import datasets as datasets_cli
from corerun.cli import finetune as finetune_cli
from corerun.cli import inference as inference_cli
from corerun.cli import tokens as tokens_cli
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://example.test/api/v1"
SERVER = "5d1f7c2a-0b3e-4a9d-8c6f-2e7b1a4d9c03"

DATASET = {
    "id": "d1", "name": "reviews", "mount_path": "/data/reviews", "source": "upload",
    "size_bytes": 2048, "file_count": 2, "format": "parquet",
    "created_at": "2026-09-27T10:00:00Z", "updated_at": "2026-09-27T10:00:00Z",
}
METADATA = {
    "dataset_id": "d1", "tenant_id": "t1", "workspace_id": "w1", "format": "parquet",
    "data_type": "text", "tags": ["nlp"], "license": "MIT", "access_level": "private",
    "custom": {"owner": "search", "draft": "yes"},
}
PLAN = {
    "model": {"id": "Qwen/Qwen3-8B", "source": "huggingface", "known": True,
              "parameters": 8_200_000_000, "dtype": "bf16", "token_on_file": False},
    "cluster": {"name": "dgx", "host": False, "connected": True, "gpu_model": "NVIDIA H100 80GB HBM3",
                "gpu_memory_gb": 80, "gpus_total": 8, "gpus_free": 6},
    "needs_gb": {"lora": 30.1, "qlora": 14.2, "full": 90.5},
    "profiles": [{"name": "gpu-1", "gpus": 1, "gpu_model": "NVIDIA H100 80GB HBM3", "gpu_memory_gb": 80, "default": True}],
    "fits": {"gpu-1": {"lora": True, "qlora": True, "full": False}},
    "recommended": {"method": "lora", "profile": "gpu-1", "reason": "The smallest profile lora fits on."},
}


@pytest.fixture
def wire(monkeypatch):
    seen = []
    answers = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        for (method, suffix), answer in answers.items():
            if request.method == method and path.endswith(suffix):
                return answer(request) if callable(answer) else httpx.Response(200, json=answer)
        return httpx.Response(404, json={"error": "not_found", "message": f"nothing at {path}"})

    transport = httpx.MockTransport(handle)
    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=transport, base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)

    # A multipart upload builds a client of its own, without the JSON
    # content type; give that one the same transport.
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=transport, **kw))

    for module in (datasets_cli, inference_cli, finetune_cli, catalogue_cli, tokens_cli):
        monkeypatch.setattr(module, "_init_client", lambda: None)
    return seen, answers


def _upload(seen):
    request = [r for r in seen if r.url.path.endswith("/data/upload")][0]
    body = request.read()
    return request, body


def _archive_from(body: bytes) -> tarfile.TarFile:
    start = body.index(b"\x1f\x8b")  # gzip magic
    return tarfile.open(fileobj=io.BytesIO(body[start:]), mode="r:gz")


# -- datasets upload ----------------------------------------------------------


def test_a_directory_is_packed_with_its_contents_at_the_root(wire, tmp_path):
    seen, answers = wire
    answers[("POST", "/data/upload")] = DATASET
    folder = tmp_path / "Reviews.v2"
    (folder / "sub").mkdir(parents=True)
    (folder / "train.csv").write_text("a,b\n1,2\n")
    (folder / "sub" / "test.csv").write_text("a,b\n3,4\n")

    ds = datasets.upload(str(folder))

    assert ds.name == "reviews"
    request, body = _upload(seen)
    assert request.url.path == "/api/v1/data/upload"
    # The name comes from the folder, made safe; the mount path from the name.
    assert b'name="name"\r\n\r\nreviews-v2' in body
    assert b'name="mount_path"\r\n\r\n/data/reviews-v2' in body
    assert b'filename="reviews-v2.tar.gz"' in body
    names = sorted(_archive_from(body).getnames())
    assert names == ["sub", "sub/test.csv", "train.csv"]


def test_a_single_file_is_packed_under_its_own_name(wire, tmp_path):
    seen, answers = wire
    answers[("POST", "/data/upload")] = DATASET
    source = tmp_path / "chats.jsonl"
    source.write_text('{"messages": []}\n')

    datasets.upload(str(source), name="support", description="Support chats")

    _, body = _upload(seen)
    assert b'name="name"\r\n\r\nsupport' in body
    assert b'name="description"\r\n\r\nSupport chats' in body
    assert _archive_from(body).getnames() == ["chats.jsonl"]


def test_an_archive_is_sent_as_it_is(wire, tmp_path):
    seen, answers = wire
    answers[("POST", "/data/upload")] = DATASET
    source = tmp_path / "images.zip"
    source.write_bytes(b"PK\x03\x04not really a zip")

    datasets.upload(str(source))

    _, body = _upload(seen)
    assert b'filename="images.zip"' in body
    assert b"PK\x03\x04not really a zip" in body
    assert b'name="name"\r\n\r\nimages' in body


def test_a_missing_path_is_refused_before_anything_is_sent(wire, tmp_path):
    seen, _ = wire
    with pytest.raises(FileNotFoundError):
        datasets.upload(str(tmp_path / "nothing"))
    assert not seen


def test_the_upload_command_reports_what_was_stored(wire, tmp_path):
    _, answers = wire
    answers[("POST", "/data/upload")] = DATASET
    (tmp_path / "a.csv").write_text("x\n1\n")

    result = runner.invoke(app, ["datasets", "upload", str(tmp_path / "a.csv"), "--name", "reviews"])
    assert result.exit_code == 0, result.output
    assert "Uploaded dataset 'reviews'" in result.output and "2.0 KB" in result.output

    as_json = runner.invoke(app, ["datasets", "upload", str(tmp_path / "a.csv"), "--json"])
    assert json.loads(as_json.stdout)["mount_path"] == "/data/reviews"


def test_an_upload_the_service_refuses_exits_non_zero(wire, tmp_path):
    _, answers = wire
    answers[("POST", "/data/upload")] = lambda r: httpx.Response(409, json={"detail": "Dataset 'reviews' already exists"})
    (tmp_path / "a.csv").write_text("x\n")
    result = runner.invoke(app, ["datasets", "upload", str(tmp_path / "a.csv")])
    assert result.exit_code == 1
    assert "already exists" in result.output


# -- datasets metadata --------------------------------------------------------


def test_only_the_fields_given_are_sent(wire):
    seen, answers = wire
    answers[("PUT", "/data/reviews/metadata")] = METADATA
    datasets.update_metadata("reviews", tags=["nlp", "en"], license="CC-BY-4.0")
    sent = json.loads([r for r in seen if r.method == "PUT"][0].content)
    assert sent == {"tags": ["nlp", "en"], "license": "CC-BY-4.0"}


def test_a_recorded_key_is_merged_not_replaced(wire):
    seen, answers = wire
    answers[("GET", "/data/reviews/metadata")] = METADATA
    answers[("PUT", "/data/reviews/metadata")] = METADATA
    datasets.update_metadata("reviews", custom={"team": "ranking"}, unset=["draft"])
    sent = json.loads([r for r in seen if r.method == "PUT"][0].content)
    # The service replaces the map whole: the key not mentioned survives.
    assert sent == {"custom": {"owner": "search", "team": "ranking"}}


def test_the_metadata_command_shows_and_changes(wire):
    seen, answers = wire
    answers[("GET", "/data/reviews/metadata")] = METADATA
    answers[("PUT", "/data/reviews/metadata")] = METADATA

    shown = runner.invoke(app, ["datasets", "metadata", "reviews"])
    assert shown.exit_code == 0, shown.output
    assert "nlp" in shown.output and "MIT" in shown.output and "owner: search" in shown.output
    assert not [r for r in seen if r.method == "PUT"]

    changed = runner.invoke(app, ["datasets", "metadata", "reviews", "--tag", "a", "--tag", "b",
                                  "--access", "team", "--set", "url=https://x?a=b"])
    assert changed.exit_code == 0, changed.output
    sent = json.loads([r for r in seen if r.method == "PUT"][0].content)
    assert sent["tags"] == ["a", "b"] and sent["access_level"] == "team"
    assert sent["custom"]["url"] == "https://x?a=b"

    cleared = runner.invoke(app, ["datasets", "metadata", "reviews", "--clear-tags"])
    assert cleared.exit_code == 0
    assert json.loads([r for r in seen if r.method == "PUT"][-1].content) == {"tags": []}


@pytest.mark.parametrize("args", [["--access", "everyone"], ["--set", "novalue"]])
def test_bad_metadata_options_are_refused(wire, args):
    seen, _ = wire
    result = runner.invoke(app, ["datasets", "metadata", "reviews", *args])
    assert result.exit_code == 1
    assert not [r for r in seen if r.method == "PUT"]


# -- inference regenerate-key -------------------------------------------------


def test_regenerating_a_key_returns_the_new_one(wire):
    seen, answers = wire
    answers[("POST", f"/inference-servers/{SERVER}/regenerate-key")] = {"api_key": "sk-new", "server_id": SERVER}
    assert inference.regenerate_key(SERVER) == "sk-new"
    assert seen[-1].method == "POST"


def test_a_running_server_answers_with_its_redeploy_and_the_key_is_read_from_it(wire):
    # A running server is redeployed with the new key, and the answer is the
    # deploy's: the whole server, with api_key set.
    _, answers = wire
    answers[("POST", f"/inference-servers/{SERVER}/regenerate-key")] = {
        "id": SERVER, "name": "mistral", "status": "deploying", "api_key": "sk-redeployed",
    }
    assert inference.regenerate_key(SERVER) == "sk-redeployed"


def test_the_regenerate_command_prints_the_key(wire):
    _, answers = wire
    answers[("POST", f"/inference-servers/{SERVER}/regenerate-key")] = {"api_key": "sk-new", "server_id": SERVER}

    # No terminal to confirm on: refused, and nothing is replaced.
    refused = runner.invoke(app, ["inference", "regenerate-key", SERVER])
    assert refused.exit_code == 2

    result = runner.invoke(app, ["inference", "regenerate-key", SERVER, "--yes"])
    assert result.exit_code == 0, result.output
    assert "sk-new" in result.output and "must switch" in result.output

    as_json = runner.invoke(app, ["inference", "regenerate-key", SERVER, "--yes", "--json"])
    assert json.loads(as_json.stdout) == {"server_id": SERVER, "api_key": "sk-new"}


# -- finetune plan ------------------------------------------------------------


def test_the_plan_asks_with_what_it_was_given(wire):
    seen, answers = wire
    answers[("GET", "/finetune/plan")] = PLAN
    assert finetune.plan("Qwen/Qwen3-8B", compute_name="dgx", batch_size=8)["recommended"]["method"] == "lora"
    params = seen[-1].url.params
    assert params["base_model"] == "Qwen/Qwen3-8B" and params["compute_name"] == "dgx"
    assert params["batch_size"] == "8" and "max_seq_length" not in params


def test_the_plan_command_shows_the_sizing(wire):
    _, answers = wire
    answers[("GET", "/finetune/plan")] = PLAN
    result = runner.invoke(app, ["finetune", "plan", "Qwen/Qwen3-8B", "--compute", "dgx"])
    assert result.exit_code == 0, result.output
    for text in ("8.2B", "lora 30.1 GB", "qlora 14.2 GB", "gpu-1", "fits", "6 of 8 GPUs free",
                 "Recommended: lora on profile gpu-1"):
        assert text in result.output, text

    as_json = runner.invoke(app, ["finetune", "plan", "Qwen/Qwen3-8B", "--json"])
    assert json.loads(as_json.stdout)["needs_gb"]["full"] == 90.5


def test_a_model_of_unknown_size_says_so(wire):
    _, answers = wire
    answers[("GET", "/finetune/plan")] = {
        "model": {"id": "x/y", "source": "huggingface", "known": False, "token_on_file": False,
                  "note": "Hugging Face did not describe this model"},
        "needs_gb": {}, "profiles": None, "fits": {},
        "recommended": {"method": "lora", "reason": "The model's size is unknown, so LoRA is the default."},
    }
    result = runner.invoke(app, ["finetune", "plan", "x/y"])
    assert result.exit_code == 0, result.output
    assert "did not describe" in result.output and "size is unknown" in result.output


# -- catalogue check ----------------------------------------------------------


def test_the_engine_comes_from_the_catalogue(wire):
    seen, answers = wire
    answers[("GET", "/inference-servers/catalog/models")] = {
        "models": [{"Slug": "resnet", "Name": "ResNet", "ExternalID": "acme/resnet", "RequiresEngine": "triton"}]
    }
    answers[("POST", "/inference-servers/catalog/check")] = {"compatible": True, "reason": ""}
    inference.check_compatibility("acme/resnet")
    body = json.loads([r for r in seen if r.url.path.endswith("/check")][0].content)
    assert body == {"model_id": "acme/resnet", "server_type": "triton"}


def test_an_image_named_needs_no_lookup(wire):
    seen, answers = wire
    answers[("POST", "/inference-servers/catalog/check")] = {"compatible": True, "reason": ""}
    inference.check_compatibility("Qwen/Qwen3-8B", image="vllm/vllm-openai:v0.11.0", quantization="fp8")
    assert len(seen) == 1
    assert json.loads(seen[0].content) == {
        "model_id": "Qwen/Qwen3-8B", "image": "vllm/vllm-openai:v0.11.0", "quantization": "fp8",
    }


def test_the_check_command_exits_one_when_it_cannot_be_served(wire):
    _, answers = wire
    answers[("GET", "/inference-servers/catalog/models")] = {"models": []}
    answers[("POST", "/inference-servers/catalog/check")] = {
        "compatible": False, "reason": "vllm 0.8.0 does not serve Qwen3_5ForCausalLM",
        "image": "vllm/vllm-openai:v0.8.0", "engine": "vllm", "engine_version": "0.8.0",
        "engine_known": True, "compatibility_url": "https://example.test/supported",
    }
    result = runner.invoke(app, ["catalogue", "check", "Qwen/Qwen3.8-27B"])
    assert result.exit_code == 1
    assert "Not compatible" in result.output and "does not serve" in result.output

    answers[("POST", "/inference-servers/catalog/check")] = {
        "compatible": True, "reason": "", "image": "vllm/vllm-openai:v0.11.0",
        "engine": "vllm", "engine_version": "0.11.0", "engine_known": True,
    }
    ok = runner.invoke(app, ["catalogue", "check", "Qwen/Qwen3-8B", "--engine", "vllm", "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["compatible"] is True


def test_the_check_is_asked_about_a_cluster(wire):
    seen, answers = wire
    answers[("POST", "/inference-servers/catalog/check")] = {
        "compatible": False, "reason": "this card's family needs vllm 0.11.0 or later",
        "image": "vllm/vllm-openai:v0.10.0", "engine": "vllm", "engine_version": "0.10.0",
        "engine_known": True, "cluster": "gb10dgx01", "serves_natively": False,
        "accelerator": {"family": "nvidia/blackwell", "name": "GB10", "source": "cluster"},
    }
    result = runner.invoke(app, ["catalogue", "check", "Qwen/Qwen3-8B", "--engine", "vllm",
                                 "--compute", "gb10dgx01", "--gpu", "1"])
    assert result.exit_code == 1
    assert "gb10dgx01" in result.output and "GB10" in result.output
    body = json.loads([r for r in seen if r.url.path.endswith("/check")][-1].content)
    assert body == {"model_id": "Qwen/Qwen3-8B", "server_type": "vllm", "compute_name": "gb10dgx01", "gpu": 1.0}


# -- tokens revoke ------------------------------------------------------------


def test_revoke_takes_the_id_as_the_list_prints_it(wire):
    seen, answers = wire
    full = "ak_0123456789abcdef0123"
    answers[("GET", "/api-keys")] = {"api_keys": [{"key_id": full, "name": "ci"}, {"key_id": "ak_zz", "name": "other"}]}
    answers[("DELETE", f"/api-keys/{full}")] = {"message": "API key revoked"}
    for reference in (full[:12], "ci"):
        result = runner.invoke(app, ["tokens", "revoke", reference, "--yes"])
        assert result.exit_code == 0, result.output
        assert seen[-1].method == "DELETE" and seen[-1].url.path.endswith(full)


def test_an_ambiguous_prefix_is_refused(wire):
    seen, answers = wire
    answers[("GET", "/api-keys")] = {"api_keys": [{"key_id": "ak_1a", "name": "a"}, {"key_id": "ak_1b", "name": "b"}]}
    result = runner.invoke(app, ["tokens", "revoke", "ak_1", "--yes"])
    assert result.exit_code == 1
    assert not [r for r in seen if r.method == "DELETE"]


def test_genai_help_names_what_is_there():
    result = runner.invoke(app, ["--help"])
    assert "retention" not in result.output
