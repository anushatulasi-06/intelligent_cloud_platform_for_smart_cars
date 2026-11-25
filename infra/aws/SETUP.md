# Private AWS native API demo setup

Author: Rishi Sheth. Current AWS credit budget is **$0**: do not create resources, install on AWS,
probe stores, start feeds, send mail or upload audio. Use local tests only until separate deployment
and cost authorization. [Option B Docker](docker/SETUP.md) is the recommended later path.

Option A runs API/worker/model on EC2 with systemd, with managed RDS, Atlas, private S3 and
SES. The frontend now runs **locally on the laptop**, calling the EC2 API through SSH. This
replaces the old public HTTP S3 website architecture; `deploy_frontend.sh` refuses publication.
No UI S3 bucket, public API port or paid HTTPS infrastructure is created by this option. Public
hosting needs a separate HTTPS/authentication design and authorization.

## 1. Plan and settings

Read [the deployment guard, eligibility, rates and zero-credit boundary](README.md). Both
options use us-west-2, x86 Linux, a private Single-AZ PostgreSQL 16 db.t4g.micro with 20 GiB,
Atlas M0 if independently available, private S3 Standard app objects and SES. There is no ALB.
Use [Option B resource settings](docker/SETUP.md#2-later-resource-plan), except Option A needs
**20 GiB EC2 gp3** and no Docker metadata-hop change. Security-group inbound remains **SSH
22 from My IP only**; API 8000 must never be opened to the public.

The planning EC2 candidate is Free-plan-listed c7i-flex.large (2 vCPU/4 GiB x86), not the old
unlisted t3.medium. It has not been AWS capacity-tested; review larger m7i-flex.large if measured
memory requires it. Listed eligibility does not mean zero credit consumption or guaranteed
regional quota. RDS “750 hours free for 12 months” refers to conditional legacy offers, not a
blanket allowance for this account. Follow the source-linked cost table in [README](README.md).

Prepare a private `backend/.env` using
[`backend.env.production.example`](backend.env.production.example), as described in
[Option B settings](docker/SETUP.md#3-code-and-private-settings). Use `APP_ENV=production`,
new random JWT/Wi-Fi secrets, real store configuration, explicit false fake-email/backup flags,
and `CORS_ORIGINS=http://127.0.0.1:5173`. Keep file permissions 0600. Validate locally:

```bash
python3 infra/aws/validate_production_env.py backend/.env
```

SES sender/recipient verification is required for application alert mail and is separate from
AWS Budgets email subscriptions. Neither a budget notification nor fake local mail proves SES.

## 2. Later guarded API installation and bootstrap

After separate authorization, set all three deployment/cost/budget variables from the
[guard instructions](README.md#deployment-guard), then run from the reviewed source checkout
on EC2 as ec2-user:

```bash
bash infra/aws/setup_ec2.sh
```

The script first validates settings, then installs Python 3.12 and backup tools/dependencies,
migrates PostgreSQL, and starts one `autosound-api` systemd worker bound to **127.0.0.1:8000**.
It never creates public demo accounts. Initialize a new empty database privately:

```bash
cd backend
.venv/bin/python -m app.db.bootstrap --initialize-empty-database
cd ..
SKIP_PULL=1 bash infra/aws/deploy_backend.sh
```

Private random account/device credentials are written to `.local_bootstrap/credentials.json`
inside `backend`, with mode 0600. The guarded deploy command restarts the API so it reloads
the newly registered active model; `SKIP_PULL=1` retains the reviewed source revision. Keep a
secure private copy and sign in with those credentials. Bootstrap also
registers the trained model, alert types, cars and devices, and refuses nonempty application
stores. It creates no historical MongoDB alerts/audio. Do not run the development seed in
production or retain its public passwords/device keys.

For optional historical seeded-alert migration, the supplied Drive folder and local key/hash
validation are in [Supplied historical audio](README.md#supplied-historical-audio). No automatic
Drive download or S3 upload is part of setup. Fresh scenario audio comes from the edge client.

## 3. Private frontend and SSH tunnel

On the laptop, install frontend dependencies/build locally using the repository's supported
Node version (22.12+ or 24), then keep this tunnel open:

```bash
ssh -N -o ExitOnForwardFailure=yes -i /private/path/key.pem \
  -L 127.0.0.1:8000:127.0.0.1:8000 ec2-user@<ec2-address>
```

From the laptop's repository checkout:

```bash
cd frontend
npm ci
VITE_USE_MOCK=false VITE_API_BASE_URL=http://127.0.0.1:8000 npm run build
npm run preview -- --host 127.0.0.1 --port 5173 --strictPort
```

Open **http://127.0.0.1:5173**. This private local preview is suitable for a reviewed class demo;
it is not a public production web server. The remote API allows this precise loopback origin.
Use the same source revision for frontend and backend. Signed private S3 media URLs still use
AWS and consume storage/request/transfer usage when used.

## 4. Later optional feeds, checks and updates

Use [Option B private edge settings and verification guidance](docker/SETUP.md#6-optional-feeds-and-verification-after-authorization).
For native deployment, the corresponding optional services are `autosound-edge` and
`autosound-demo-data`. They require explicit feed cost acknowledgement and enable flags on
setup runs; omission stops/disables a previously enabled feed. `--demo-only` position history
preserves real history and all alerts. In private `infra/aws/edge.env`, `EDGE_CLIENT=replay_client`
is the default, matching the Docker option. It plays bundled recorded CARLA frames/telemetry,
needs no GPU/server, and captures audio at the recording's event. Choose a scenario first;
Start/Stop route then resume/pause playback. Set `EDGE_CLIENT=demo_client` for the illustrated
virtual car. The shared preflight rejects unknown selectors before service changes; rerun the
guarded setup with the desired feed flags after changing the private file.

Replay camera PUTs occur up to every second while frames advance and every five seconds while
idle/paused (about $0.018/$0.0036 per hour at the reviewed rate). The virtual alternative keeps
its two-second camera (about $0.009/hour). These exclude other usage and are planning assumptions,
not spending caps; no AWS activity is authorized at budget zero. Live CARLA, unlike replay, needs
a separately reviewed simulator machine and private device keys through an SSH API tunnel.

After authorization, `deploy_backend.sh` checks consent and production settings before git
pulls, dependency changes, migrations and service restarts. It does not seed accounts. Retain
private settings and review forward-only migrations before updates/rollback. External checks,
readiness probes, automated live demos, mail and backups all consume AWS usage and are
blocked by the current zero-credit instruction.

Stopping services or EC2 is not teardown. EBS/EIP/RDS storage/S3/backups remain billed; RDS
restarts after seven days. Follow the [reviewed cleanup plan](docker/SETUP.md#7-updates-and-cleanup-after-authorization)
and the retained-cost explanation in [README](README.md#planning-costs-before-credits).
