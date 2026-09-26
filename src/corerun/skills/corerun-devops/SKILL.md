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
2. **The encryption key.** Every stored credential and the sign-in signing
   keys are encrypted with it. The chart generates it on the first install
   and keeps it on every upgrade; **copy it out of the cluster straight
   away** and keep it with the backups -- a database, or a backup of one,
   cannot be read without it, and it can never be changed:
   `kubectl get secret corerun-secrets -n corerun -o jsonpath='{.data.db-encryption-key}' | base64 -d`.
   For GitOps or `helm template` (which cannot see the cluster), set
   `secrets.dbEncryptionKey` and `postgresql.auth.password` yourself or use
   `secrets.existingSecret`; the chart refuses an upgrade it cannot keep the
   key through.
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

- Back up first, and read the release notes: a release that changes a table
  says so. Rolling back does not undo a schema change.
- Keep `values.yaml` in version control and upgrade from it. `--reuse-values`
  keeps what the last release had but silently drops any `--set` from a
  failed upgrade; prefer the file.
- Each service updates its own tables at start. There are **no migration
  steps and no backward conversions**: a release that changes a table's
  shape says so in its notes, and that change is done once, by hand, with a
  backup taken first.
- The generated keys (encryption key, database password, bootstrap and
  operator tokens, mint key, git secret) are kept across upgrades, read back
  from the release's Secret.
- The route table, gatekeeper policy and scopes are ConfigMaps
  (`corerun-gatekeeper-config`, `corerun-auth-config`) the chart renders. A
  hand-edit is overwritten by the next upgrade.

## Backups

```yaml
postgresql:
  backup:
    enabled: true
    endpoint: https://s3.example.com
    bucket: corerun-backups
    existingSecret: corerun-backup-credentials   # accessKey, secretKey
gitServer:
  backup:
    enabled: true
    endpoint: https://s3.example.com
    bucket: corerun-backups
```

Nightly dumps of every database (and the GenAI one), and an archive of the git
repositories, the newest seven of each kept. Object storage is backed up by
the storage system itself (snapshots, replication). **None of it restores
without the encryption key** -- keep it with the dumps, outside the cluster.

Run one now, and check it worked:

```bash
kubectl create job -n corerun --from=cronjob/corerun-postgres-backup backup-now
kubectl logs -n corerun job/backup-now -f
```

Restoring: install with the same key, services scaled to zero, `pg_restore`
each dump, scale up. The install's docs have it step by step (Guides → Backup
and restore). Test a restore before you need one.

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

The chart ships its alert rules as a PrometheusRule: pods down or restarting,
gateway errors, authorization failing, a cluster disconnected, the
break-glass used, a refresh token reused, background tasks stalled, the
database volume filling, a backup that has not succeeded in 36 hours, and audit
events dropped on the way to the trail or a SIEM.
Where they are sent is Alertmanager's receiver:

```bash
ALERT_WEBHOOK_URL=https://… ALERT_EMAIL_TO=oncall@example.com \
  ALERT_SMTP_HOST=smtp.example.com:587 ALERT_SMTP_FROM=alerts@example.com \
  deploy/monitoring/install.sh
```

**Audit and SIEM.** Every change, sign-in and refused request is one JSON line
on the API's stdout with `"log":"audit"` (any log shipper picks it up with no
configuration), a row the organisation reads under Organization → Audit, and --
when the install names one -- an event at the site's SIEM:

```yaml
audit:
  syslog: {address: siem.example.com:6514, transport: tcp+tls, format: cef}
  webhook: {url: https://splunk.example.com:8088/services/collector/raw, existingSecret: audit-webhook}
  retentionDays: 365
```

Each organisation can add its own destinations too (`corerun org audit add`).

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

- Do not change the encryption key on a running install, and never lose it.
- Do not `kubectl delete pvc` in the namespace unless the install is meant to be
  gone, data and all.
- Do not edit ConfigMaps the chart owns; change values and upgrade.
- Do not run commands on a customer's hosts or clusters for them; hand them
  the command.
- Do not print or paste secrets. Read one only to hand it to the person who
  needs it: `kubectl get secret corerun-secrets -n corerun -o jsonpath='{.data.<key>}' | base64 -d`.
