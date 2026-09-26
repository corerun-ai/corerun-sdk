---
name: corerun-devops
description: Install, upgrade, back up, monitor and troubleshoot an on-premises corerun installation with Helm on Kubernetes -- the enterprise edition, its licence, first-run bootstrap, identity provider, object store, compute onboarding and secrets. Use when asked to deploy corerun on a customer's own cluster, upgrade or roll it back, connect a GPU cluster or bare-metal host to it, recover sign-in, set up monitoring, or work out why an install is not working.
---

# Running corerun on premises

This is the person with a kubeconfig. Everything here happens with `helm` and
`kubectl` against the cluster corerun runs in; what happens *inside* the
organisation afterwards -- people, sign-in providers, workspaces, quotas -- is
the `corerun-admin` skill.

The full guides are served by the install itself at `https://<domain>/docs/`
(Guides → Single-tenant deployment, Offline deployment). This skill is the
shape of the job and the things that go wrong.

## What gets installed

One Helm release, namespace `corerun` by convention:

| Component | What it is |
|---|---|
| gatekeeper | The front door. Routes and authorizes every request |
| auth | Sign-in and the only holder of a signing key |
| api, hub | The API, and the tunnel to every connected cluster |
| ui, data, docs | The console, the dataset service, these docs |
| postgresql | Everything durable. Also holds OpenFGA's data and the sealed signing keys |
| openfga | Authorization |
| objectio (optional) | S3 object storage run by the chart |
| git (optional) | Model registry and code repositories |
| genai (optional) | Traces, judges, evaluations |

## Before installing: decide these, they are hard to change later

1. **Edition.** `edition: enterprise`. Forgetting it installs the hosted
   product's surface, runs unlicensed and nobody notices.
2. **Secrets.** `secrets.jwtSecret`, `secrets.apiKeySecret` and
   `secrets.dbEncryptionKey` default to development strings. Generate each with
   `openssl rand -hex 32` before the first install and **keep them for the life
   of the install**: the database encryption key decrypts every stored
   credential and the sealed signing keys, and a changed or lost one makes
   them unreadable. Keep a copy outside the cluster.
3. **Hostname.** `global.domain` and `global.publicUrl`. Tokens are issued for
   this address; changing it later signs everyone out and breaks every
   connected cluster's operator.
4. **Where storage is.** `objectStore.enabled: true` (the chart runs ObjectIO)
   or `bootstrap.storage.*` (the site's own S3/Ceph/MinIO/Azure). The model
   registry's weights location (`objectStore.storagePlane`, `modelRegistry.lfs.*`)
   must be decided before the first model is stored.
5. **Where compute is.** `bootstrap.attachSelf: true` makes this cluster the
   organisation's compute. Otherwise onboard GPU clusters and hosts afterwards.
6. **How people sign in.** An OIDC provider (`bootstrap.sso.*`), or a local
   administrator (`localAccounts.enabled` + `bootstrap.localAdmin.enabled`), or
   both. Register `https://<domain>/auth/callback` as the redirect URI.

## Installing

```bash
kubectl create namespace corerun
kubectl create secret generic corerun-sso -n corerun \
  --from-literal=client-secret='<client secret>'

helm upgrade --install corerun oci://ghcr.io/corerun-ai/charts/corerun \
  --version <chart version> -n corerun -f values.yaml --wait --timeout 15m
```

A minimal `values.yaml`:

```yaml
edition: enterprise
global:
  domain: corerun.example.com
  publicUrl: https://corerun.example.com
secrets:
  jwtSecret: <openssl rand -hex 32>
  apiKeySecret: <openssl rand -hex 32>
  dbEncryptionKey: <openssl rand -hex 32>
bootstrap:
  enabled: true
  attachSelf: true
  tenant:
    slug: acme
    displayName: Acme
    adminEmail: admin@acme.com      # the account you will actually sign in with
  sso:
    provider: entra
    issuerUrl: https://login.microsoftonline.com/<directory-id>/v2.0
    clientId: <application id>
    existingSecret: corerun-sso
objectStore:
  enabled: true
ingress:
  enabled: true
  className: nginx
  tls:
    - secretName: corerun-tls
      hosts: [corerun.example.com]
```

**Always `--wait`.** The organisation is created by a post-install hook; without
it the command returns before anything exists. Check it:

```bash
kubectl logs -n corerun job/corerun-bootstrap-tenant
kubectl get pods -n corerun
curl -s https://corerun.example.com/api/v1/bootstrap/status
```

TLS ends at the ingress or the load balancer in front of it. The gatekeeper
holds no certificate and does no ACME.

**No internet?** Mirror the images first and point the chart at the mirror
(`image.registry`, `imageRegistryOverride`, `global.imagePullSecrets`). Two
things degrade and nothing breaks: the daily model-recipe refresh keeps what it
last had, and weights cannot be pulled from Hugging Face -- bring them into
the registry instead. See Guides → Offline deployment.

## The licence

A new install runs a 60-day trial, then refuses new work until licensed.
Nothing already running stops. Two ways to install a licence file:

```bash
corerun org license install ./license.json     # an organisation administrator; takes effect at once
corerun org license show
```

or as a Secret, which survives a reinstall:

```bash
kubectl create secret generic corerun-license -n corerun --from-file=license.json=./license.json
helm upgrade corerun oci://ghcr.io/corerun-ai/charts/corerun -n corerun \
  --reuse-values --set license.existingSecret=corerun-license
```

A licence installed through the console or CLI wins over the file. Every API
response carries `X-corerun-License-State` (`licensed`, `grace`,
`restricted`).

## Connecting compute

A GPU cluster somewhere else, or a bare-metal machine, joins through an
outbound connection from its operator to this install -- nothing needs to reach
into it. An organisation administrator prepares it and gets the manifest or
the install command:

```bash
corerun clusters add gpu-east --arch amd64 --gpu h100 --tenant-wide > gpu-east.yaml
kubectl --context gpu-east apply -f gpu-east.yaml

corerun hosts add dgx01 --gpu gb10 --tenant-wide
```

`--gpu` names the cards -- a family (`hopper`) or a card (`h100`); check
with `corerun accelerators show <value>`. The host command prints two
commands for the machine's own administrator to run on it: one installs the
operator (and Incus on Linux), one joins with a one-time token. **Never run anything on a customer's host on their
behalf.** Removing a host is the host's administrator running
`sudo corerun-host-operator-uninstall` (`--purge` also removes its containers
and data), then:

```bash
corerun hosts rm dgx01 --tenant-wide
corerun clusters rm gpu-east --tenant-wide
```

A remote cluster needs this install's public address for its operator
(`wss://<domain>/operators`). If an operator never connects, check that
address from the remote side first.

## Upgrading

```bash
helm upgrade corerun oci://ghcr.io/corerun-ai/charts/corerun --version <new> \
  -n corerun -f values.yaml --wait --timeout 15m
kubectl rollout status deployment -n corerun
helm rollback corerun -n corerun          # back to the previous release
```

- Keep `values.yaml` in version control and upgrade from it. `--reuse-values`
  keeps what the last release had but silently drops any `--set` from a
  failed upgrade; prefer the file.
- Each service updates its own tables at start. There are **no migration
  steps and no backward conversions**: a release that changes a table's
  shape says so in its notes, and that change is done once, by hand, with a
  backup taken first.
- The generated keys (bootstrap token, operator token, mint key, git secret)
  are kept across upgrades. The three `secrets.*` values are only kept if
  they are in `values.yaml`.
- The route table, gatekeeper policy and scopes are ConfigMaps
  (`corerun-gatekeeper-config`, `corerun-auth-config`) the chart renders. A
  hand-edit is overwritten by the next upgrade.

## Backups

The chart backs up **only the git repositories** (`gitServer.backup.enabled`,
nightly to a bucket, seven kept). Everything else is yours to arrange, and an
install without it cannot be recovered:

| What | How |
|---|---|
| PostgreSQL: organisations, people, workloads, signing keys, authorization, the licence | `pg_dump` both databases nightly (`corerun`, and `corerun_genai` if GenAI is on), or a volume snapshot of `data-corerun-postgresql-0` |
| `corerun-secrets` and the `secrets.*` values | Outside the cluster, with the database backups. **A database backup is useless without `dbEncryptionKey`.** |
| Object storage (ObjectIO or the site's S3) | The storage system's own replication or snapshots |
| `values.yaml`, the licence file | Version control |

```bash
kubectl exec -n corerun corerun-postgresql-0 -- pg_dump -U corerun -Fc corerun > corerun-$(date +%F).dump
```

Restore into an empty database before the API starts, with the same
`dbEncryptionKey`. Test a restore before you need one.

`helm uninstall` leaves the database volume; deleting the PVCs deletes
everything.

## Monitoring

Every service serves `/metrics` on its own port (9090), never through the
front door. With the Prometheus Operator installed:

```bash
helm upgrade corerun oci://ghcr.io/corerun-ai/charts/corerun -n corerun -f values.yaml \
  --set monitoring.podMonitors=true --set monitoring.dashboards=true \
  --set tracing.endpoint=otel-collector.monitoring:4317
```

Dashboards land in a **corerun** folder: platform (start here), capacity,
gatekeeper, hub, credentials, registry, object storage, trace a request.

**The chart ships no alert rules.** Alert at least on: pods not ready,
`corerun_hub_agents_connected` falling, gatekeeper `check_failures`, the auth
signing key's age, `corerun_auth_break_glass_total` above zero, and the
database volume filling.

Every response carries `X-Request-Id`. Paste it into the "trace a request"
dashboard, or grep every service's logs for it.

## When something is wrong

| Symptom | Look at |
|---|---|
| Install hangs or the hook fails | `kubectl logs -n corerun job/corerun-bootstrap-tenant`. `Could not validate OIDC issuer` means the cluster cannot reach the IdP or the tenant id is wrong |
| Signs in, lands on "Access pending" | `bootstrap.tenant.adminEmail` is not the account used. Fix the value and upgrade; provisioning is idempotent |
| Identity provider broken, nobody can sign in | New client secret: update the `existingSecret` and `helm upgrade`. A local administrator (`localAccounts.enabled`) is the way in that does not need the IdP -- arm it before you need it |
| Everything answers 403 after a change | The gatekeeper runs an old route table or model. `kubectl rollout restart deployment/corerun-gatekeeper -n corerun` |
| A route answers 404 that should exist | Check the edition first. `/api/v1/admin/*` answers 404 on enterprise by design |
| Pods in `ImagePullBackOff`, "manifest unknown" | The tag does not exist in the registry or the mirror |
| New work refused, running work fine | The licence: `corerun org license show` |
| A cluster shows disconnected | Its operator's logs: `kubectl logs -n corerun -l app=corerun-cluster-operator` on that cluster |
| Object store pod crash-loops | ObjectIO needs io_uring; the node's seccomp must allow it |
| Console blank after an upgrade | Browser cache of the previous bundle; hard-reload |

Health: `/health` on api, auth and gatekeeper; `/api/v1/bootstrap/status` for
whether the install is configured.

## Do not

- Do not change `dbEncryptionKey` on a running install, and never lose it.
- Do not `kubectl delete pvc` in the namespace unless the install is meant to be
  gone, data and all.
- Do not edit ConfigMaps the chart owns; change values and upgrade.
- Do not run commands on a customer's hosts or clusters for them; hand them
  the command.
- Do not print or paste secrets. Read one only to hand it to the person who
  needs it: `kubectl get secret corerun-secrets -n corerun -o jsonpath='{.data.<key>}' | base64 -d`.
