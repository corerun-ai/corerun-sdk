"""`corerun policies try`: a command read as the request the platform decides,
the same way the console's Try it reads it, and the decision asked for."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import kubectl
from corerun.cli import policies as policies_cli
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://example.test/api/v1"

# The console's own table (corerun-ui/src/agents/kubectl.test.ts): the two
# readers must agree, or the CLI and the console would answer differently.
CASES = [
    ("kubectl get pods -A", {"action": "list", "resource": "pods", "namespace": ""}),
    ("kubectl get po -n prod", {"action": "list", "resource": "pods", "namespace": "prod"}),
    ("kubectl get deploy/api -n prod", {"action": "get", "group": "apps", "resource": "deployments", "namespace": "prod", "name": "api"}),
    ("kubectl describe deployment api --namespace=staging", {"action": "get", "group": "apps", "resource": "deployments", "namespace": "staging", "name": "api"}),
    ("kubectl get nodes", {"action": "list", "resource": "nodes", "namespace": ""}),
    ("kubectl delete deploy api -n prod", {"action": "delete", "group": "apps", "resource": "deployments", "namespace": "prod", "name": "api"}),
    ("kubectl delete pods -n scratch", {"action": "deletecollection", "resource": "pods", "namespace": "scratch"}),
    ("kubectl logs pod/web-1 -n prod", {"action": "get", "resource": "pods", "subresource": "log", "namespace": "prod", "name": "web-1"}),
    ("kubectl logs web-1", {"action": "get", "resource": "pods", "subresource": "log", "namespace": "default", "name": "web-1"}),
    ("kubectl scale deploy api --replicas 2 -n prod", {"action": "patch", "group": "apps", "resource": "deployments", "subresource": "scale", "namespace": "prod", "name": "api"}),
    ("kubectl rollout restart deploy/api -n prod", {"action": "patch", "group": "apps", "resource": "deployments", "namespace": "prod", "name": "api"}),
    ("kubectl rollout status deploy/api", {"action": "get", "group": "apps", "resource": "deployments", "name": "api"}),
    ("kubectl exec -it web-1 -n prod -- sh", {"action": "create", "resource": "pods", "subresource": "exec", "namespace": "prod", "name": "web-1"}),
    ("kubectl create deployment api --image=nginx -n team-a", {"action": "create", "group": "apps", "resource": "deployments", "namespace": "team-a", "name": "api"}),
    ("kubectl create ns team-b", {"action": "create", "resource": "namespaces", "namespace": "", "name": "team-b"}),
    ("kubectl get cj,jobs -n batch", {"action": "list", "group": "batch", "resource": "cronjobs", "namespace": "batch"}),
    ("kubectl get ing -n web", {"action": "list", "group": "networking.k8s.io", "resource": "ingresses", "namespace": "web"}),
    ("helm list -A", {"action": "list", "resource": "secrets", "namespace": "", "labels": ["owner=helm"]}),
    ("helm upgrade web ./chart -n prod", {"action": "create", "resource": "secrets", "namespace": "prod", "name": "sh.helm.release.v1.web.v1"}),
    ("helm uninstall web -n prod", {"action": "delete", "resource": "secrets", "namespace": "prod", "name": "sh.helm.release.v1.web.v1"}),
]


@pytest.mark.parametrize("line, want", CASES)
def test_reads_commands_as_the_console_does(line, want):
    request, _ = kubectl.parse(line)
    for key, value in want.items():
        assert request.get(key) == value, (line, key, request)


@pytest.mark.parametrize("line", ["", "ls -la", "kubectl apply -f x.yaml", "kubectl frobnicate", "kubectl get", "helm upgrade"])
def test_refuses_what_it_cannot_read(line):
    with pytest.raises(kubectl.ParseError):
        kubectl.parse(line)


@pytest.fixture
def wire(monkeypatch):
    seen, answers = [], {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for (method, suffix), answer in answers.items():
            if request.method == method and request.url.path.endswith(suffix):
                return httpx.Response(200, json=answer)
        return httpx.Response(404, json={"error": "not_found", "message": request.url.path})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    monkeypatch.setattr(policies_cli, "_init_client", lambda: None)
    return seen, answers


def _app():
    from corerun.cli import app
    return app


def test_without_an_agent_every_policy_in_force_decides(wire, tmp_path):
    seen, answers = wire
    answers[("GET", "/policies")] = {"policies": [
        {"id": "p1", "name": "guardrails", "level": "organization", "enabled": True,
         "rules": [{"name": "kube-system", "effect": "deny", "match": {"namespace": ["kube-system"]}}]},
        {"id": "p2", "name": "old", "level": "workspace", "enabled": False, "rules": [{"name": "x", "effect": "allow"}]},
    ]}
    answers[("POST", "/toolgate/explain")] = {"effect": "deny", "rule": "kube-system", "layer": "organization:guardrails", "message": "kube-system belongs to the platform team"}
    draft = tmp_path / "careful.yaml"
    draft.write_text("rules:\n  - name: ask-before-scaling\n    effect: ask\n    match:\n      subresource: [scale]\n")
    result = runner.invoke(_app(), ["policies", "try", "kubectl delete pod x -n kube-system", "-f", str(draft)])
    assert result.exit_code == 0, result.output
    body = json.loads([r for r in seen if r.url.path.endswith("/toolgate/explain")][0].read())
    assert body["request"]["namespace"] == "kube-system" and body["request"]["action"] == "delete"
    assert [r["policy"] for r in body["rules"]] == ["organization:guardrails", "workspace:draft"]  # the disabled one left out
    assert "Refused" in result.output and "guardrails" in result.output and "platform team" in result.output


def test_a_command_it_cannot_read_sends_nothing(wire):
    seen, _ = wire
    result = runner.invoke(_app(), ["policies", "try", "kubectl apply -f x.yaml"])
    assert result.exit_code == 1 and "in the file" in result.output
    assert not seen
