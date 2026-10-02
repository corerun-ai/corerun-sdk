"""
A kubectl or helm command line read as the request the platform would decide.

The API server's verb, the resource's group and lowercase plural, subresource,
namespace and name -- what the platform's Kubernetes adapter hands its policy
engine. An approximation of the first request a command makes, which is the
one a rule is usually about. The console's "Try it" reads commands the same
way (corerun-ui/src/agents/kubectl.ts); the two are kept in step.
"""

from __future__ import annotations

import re
import shlex
from typing import Dict, List, Optional, Tuple

_RESOURCES: Dict[str, Tuple[str, str]] = {}


def _add(group: str, plural: str, *names: str) -> None:
    for n in (plural, *names):
        _RESOURCES[n] = (group, plural)


_add("", "pods", "pod", "po")
_add("", "services", "service", "svc")
_add("", "configmaps", "configmap", "cm")
_add("", "secrets", "secret")
_add("", "namespaces", "namespace", "ns")
_add("", "nodes", "node", "no")
_add("", "persistentvolumeclaims", "persistentvolumeclaim", "pvc")
_add("", "persistentvolumes", "persistentvolume", "pv")
_add("", "serviceaccounts", "serviceaccount", "sa")
_add("", "events", "event", "ev")
_add("", "endpoints", "ep")
_add("apps", "deployments", "deployment", "deploy")
_add("apps", "statefulsets", "statefulset", "sts")
_add("apps", "daemonsets", "daemonset", "ds")
_add("apps", "replicasets", "replicaset", "rs")
_add("batch", "jobs", "job")
_add("batch", "cronjobs", "cronjob", "cj")
_add("networking.k8s.io", "ingresses", "ingress", "ing")
_add("autoscaling", "horizontalpodautoscalers", "horizontalpodautoscaler", "hpa")

_VALUED = {"f", "filename", "l", "selector", "o", "output", "c", "container", "replicas",
           "type", "image", "set", "values", "version"}
_CLUSTER_SCOPED = {"namespaces", "nodes", "persistentvolumes"}


class ParseError(ValueError):
    """The command could not be read as a request."""


def _resource(word: str) -> Optional[Tuple[str, str]]:
    return _RESOURCES.get(word.lower().split(",")[0].split(".")[0])


def _req(**kw) -> Dict:
    r = {"action": "", "group": "", "resource": "", "subresource": "", "namespace": "", "name": ""}
    r.update({k: v for k, v in kw.items() if v is not None})
    return r


def _split(args: List[str]):
    positional, flags, values = [], set(), {}
    namespace, every = "default", False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        if a in ("-A", "--all-namespaces"):
            every = True
        elif a in ("-n", "--namespace"):
            if i + 1 < len(args):
                i += 1
                namespace = args[i]
        elif a.startswith("--namespace="):
            namespace = a[len("--namespace="):]
        elif a.startswith("-"):
            k, eq, v = a.lstrip("-").partition("=")
            flags.add(k)
            if eq:
                values[k] = v
            elif k in _VALUED and i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 1
                values[k] = args[i]
        else:
            positional.append(a)
        i += 1
    return positional, namespace, every, flags, values


def _target(pos: List[str]):
    if not pos:
        return None
    first = pos[0]
    if "/" in first:
        kind, name = first.split("/", 1)
        found = _resource(kind)
        return (found[0], found[1], name) if found else None
    found = _resource(first)
    return (found[0], found[1], pos[1] if len(pos) > 1 else "") if found else None


def parse(line: str) -> Tuple[Dict, Optional[str]]:
    """(request, note) for a command line; ParseError for one it cannot read."""
    try:
        words = shlex.split(line.strip())
    except ValueError as e:
        raise ParseError(str(e))
    if not words:
        raise ParseError("Type a kubectl or helm command")
    tool = words[0]
    if tool == "helm":
        return _helm(words[1:])
    if tool not in ("kubectl", "k"):
        raise ParseError("Only kubectl and helm commands can be tried")
    if len(words) < 2:
        raise ParseError("kubectl what?")
    verb, rest = words[1], words[2:]
    pos, namespace, every, flags, values = _split(rest)
    ns = "" if every else namespace
    t = _target(pos)
    unknown = ParseError(f"kubectl {verb}: which resource? e.g. kubectl {verb} deploy/api -n prod")

    if verb in ("get", "describe"):
        if not t:
            raise unknown
        group, resource, name = t
        labels = values.get("l") or values.get("selector")
        return (_req(action="get" if name else "list", group=group, resource=resource,
                     namespace="" if resource in _CLUSTER_SCOPED else ns, name=name,
                     labels=labels.split(",") if labels else None),
                "then watches for changes" if ("w" in flags or "watch" in flags) else None)
    if verb == "logs":
        first = pos[0] if pos else ""
        name = first.split("/", 1)[1] if "/" in first else first
        if not name:
            raise ParseError("kubectl logs: which pod?")
        if "/" in first and not first.startswith("pod"):
            found = _resource(first.split("/")[0]) or ("", "")
            return _req(action="get", group="apps", resource=found[1], namespace=ns, name=name), "finds the pod first, then reads its log"
        return _req(action="get", resource="pods", subresource="log", namespace=ns, name=name), None
    if verb == "delete":
        if not t:
            raise unknown
        group, resource, name = t
        return _req(action="delete" if name else "deletecollection", group=group, resource=resource, namespace=ns, name=name), None
    if verb == "create":
        first = pos[0] if pos else ""
        found = ("", "secrets") if first == "secret" else _resource(first)
        if not found:
            if values.get("f") or values.get("filename"):
                raise ParseError("The resource is in the file: try e.g. kubectl create deployment api -n prod")
            raise unknown
        name = (pos[2] if len(pos) > 2 else "") if first == "secret" else (pos[1] if len(pos) > 1 else "")
        return _req(action="create", group=found[0], resource=found[1],
                    namespace="" if found[1] == "namespaces" else ns, name=name), None
    if verb in ("apply", "replace"):
        raise ParseError(f"kubectl {verb} -f: the resource is in the file -- try the same as e.g. kubectl patch deploy api -n prod")
    if verb in ("edit", "patch", "label", "annotate", "set", "scale", "autoscale"):
        tt = _target(pos[1:] if verb == "set" else pos)
        if not tt:
            raise unknown
        return _req(action="patch", group=tt[0], resource=tt[1], subresource="scale" if verb == "scale" else "",
                    namespace=ns, name=tt[2]), None
    if verb == "rollout":
        sub = pos[0] if pos else ""
        tt = _target(pos[1:])
        if not tt:
            raise unknown
        if sub in ("status", "history"):
            return _req(action="get", group=tt[0], resource=tt[1], namespace=ns, name=tt[2]), None
        return (_req(action="patch", group=tt[0], resource=tt[1], namespace=ns, name=tt[2]),
                f"rollout {sub} patches the {re.sub(r's$', '', tt[1])}")
    if verb in ("exec", "attach", "port-forward", "cp"):
        first = pos[0] if pos else ""
        name = first.split("/", 1)[1] if "/" in first else first
        sub = {"port-forward": "portforward", "cp": "exec"}.get(verb, verb)
        return (_req(action="create", resource="pods", subresource=sub, namespace=ns, name=name),
                "the platform never allows this, whatever a policy says")
    if verb == "top":
        first = pos[0] if pos else ""
        nodes = first in ("node", "nodes")
        return _req(action="list", group="metrics.k8s.io", resource="nodes" if nodes else "pods",
                    namespace="" if first.startswith("node") else ns), None
    if verb in ("cordon", "uncordon", "drain"):
        return _req(action="patch", resource="nodes", name=pos[0] if pos else ""), None
    if verb in ("api-resources", "api-versions", "version", "cluster-info"):
        return _req(action="get", flags=["discovery"]), "the API's own discovery"
    raise ParseError(f"kubectl {verb} is not one this can read -- try get, describe, logs, create, delete, patch, scale, rollout or exec")


def _helm(args: List[str]) -> Tuple[Dict, Optional[str]]:
    """helm keeps a release in a Secret named sh.helm.release.v1.<release>.v<n>, labelled owner=helm."""
    verb = args[0] if args else ""
    pos, namespace, every, _, _ = _split(args[1:])
    ns = "" if every else namespace
    release = pos[0] if pos else ""
    record = f"sh.helm.release.v1.{release}.v1" if release else ""
    if verb in ("list", "ls"):
        return (_req(action="list", resource="secrets", namespace=ns, labels=["owner=helm"]),
                "helm lists its release records, Secrets labelled owner=helm")
    if verb in ("status", "get", "history"):
        return _req(action="get", resource="secrets", namespace=ns, name=record), "helm reads its release record"
    if verb in ("install", "upgrade", "rollback"):
        if not release:
            raise ParseError(f"helm {verb}: which release?")
        return (_req(action="create", resource="secrets", namespace=ns, name=record),
                None if verb == "rollback" else "helm records the release, then creates or changes what the chart holds -- each of those is decided too")
    if verb in ("uninstall", "delete"):
        if not release:
            raise ParseError(f"helm {verb}: which release?")
        return _req(action="delete", resource="secrets", namespace=ns, name=record), "and deletes what the chart made -- each decided too"
    raise ParseError(f"helm {verb} is not one this can read -- try list, status, install, upgrade, uninstall or rollback")


def describe(r: Dict) -> str:
    """The request in one line, for showing what was understood."""
    what = (r["resource"] + (f"/{r['subresource']}" if r.get("subresource") else "")) if r.get("resource") else "(discovery)"
    parts = [r["action"], what]
    if r.get("name"):
        parts.append(f"“{r['name']}”")
    if r.get("namespace"):
        parts.append(f"in {r['namespace']}")
    elif r.get("resource") and r["resource"] not in _CLUSTER_SCOPED:
        parts.append("in every namespace")
    if r.get("labels"):
        parts.append("labelled " + ", ".join(r["labels"]))
    return " ".join(p for p in parts if p)
