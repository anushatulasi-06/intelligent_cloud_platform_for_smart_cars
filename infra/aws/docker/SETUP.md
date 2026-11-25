# Private AWS Docker demo setup

Author: Rishi Sheth. **Do not execute AWS steps while the authorized credit budget is $0.**
This is the recommended later deployment path for C1–C5. It uses one x86 EC2 host for API,
worker/model and nginx UI; PostgreSQL is RDS, MongoDB is Atlas, objects are private S3, and
alert mail uses SES. Local PostgreSQL/MongoDB containers are not part of this AWS option.
Read [safety rules and exact cost assumptions](../README.md) first. No ALB or public UI bucket
is needed. The private SSH workflow replaces the old public HTTP instructions.

## 1. Authorize and size before creating anything

Confirm the account is still Free, its credit balance/expiry, allowed instance types and region.
The planning candidate is **c7i-flex.large, 2 vCPU/4 GiB, x86**, in **us-west-2**. It is currently
listed Free-plan eligible but consumes credits and has not been tested for this AWS workload.
If memory/build pressure needs 8 GiB, review m7i-flex.large and its higher cost before changing
anything. Do not switch to ARM: current backup tooling is amd64. Do not upgrade the account,
activate advanced features, or launch a GPU instance to work around a restriction.

After separate authorization of a positive planning budget, set the three variables documented
in [the deployment guard](../README.md#deployment-guard) on the EC2 shell. Missing consent or a
zero budget blocks the scripts before installation/downloads. Console resource creation must
also wait for that authorization. An AWS budget email is an alert, not a hard cap.

## 2. Later resource plan

Use us-west-2 throughout; review the console estimate before confirming each creation.

| Resource | Private demo settings |
|---|---|
| EC2 | Amazon Linux 2023, x86_64, candidate c7i-flex.large; **30 GiB gp3**, baseline IOPS/throughput |
| Security group | **SSH 22 from My IP only**. No inbound 80, 8000, database or CARLA ports |
| Address | One public IPv4/Elastic IP for SSH; charged both in use and idle |
| Instance role | `autosound-ec2-role`, EC2 trust, [`iam-instance-policy.json`](../iam-instance-policy.json) scoped to the app bucket and SES |
| Metadata | IMDS response hop limit 2 so backend containers can use the instance role |
| RDS | PostgreSQL 16, **Single-AZ db.t4g.micro, 20 GiB gp2/gp3**, private access, database `autosound`; allow PostgreSQL only from the EC2 security group |
| Atlas | M0 in AWS Oregon if independently available; private password user, allow only the EC2 address `/32` |
| S3 | One uniquely named private app-data bucket; keep Block Public Access on; no public policy |
| SES | Verify chosen sender and recipient identities in Oregon; sandbox restrictions apply |

Do not enable Multi-AZ, provisioned IOPS, public RDS, NAT, ALB, paid email add-ons or extra logging
without a new cost review. Choose self-managed RDS credentials to avoid silently adding a
Secrets Manager subscription. Never store AWS access keys in `backend/.env`; use the instance role.
SES identity verification enables application alert delivery only. Budget-email recipients belong
to AWS Budgets separately; a budget notification is not evidence that SES alert mail works.

## 3. Code and private settings

Once the resource plan is authorized, SSH with the key pair scoped to this host. Clone the
public repository or copy only tracked source; never transfer local `.env`, databases,
`node_modules`, virtualenvs, datasets or private credential bundles. Record the deployed commit.
The setup wrapper can create `backend/.env` from the production template and stop, but does
so only after consent. Alternatively prepare the file locally and transfer it privately.

Populate [`backend.env.production.example`](../backend.env.production.example):

- `APP_ENV=production`, private RDS/Atlas URLs, the app bucket and `AWS_REGION=us-west-2`.
- Generate a new JWT with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
- Generate a Wi-Fi key with `python3 -c "from base64 import urlsafe_b64encode; import secrets; print(urlsafe_b64encode(secrets.token_bytes(32)).decode())"`.
- `CORS_ORIGINS=http://127.0.0.1:5173`; leave S3 endpoint overrides blank for AWS S3.
- Verified `SES_FROM_EMAIL`, `DEV_FAKE_EMAIL=false`, `DEV_FAKE_BACKUP=false`.

Keep secrets in the private file with permissions 0600 and retain the Wi-Fi encryption key
securely. Run the **local-only** preflight before installing anything:

```bash
python3 infra/aws/validate_production_env.py backend/.env
```

It rejects placeholders, blank/default secrets, invalid Wi-Fi keys, duplicate settings and public
HTTP CORS origins without printing their values. The production application validates again.

## 4. Later install and private bootstrap

Only after the approved budget/consent variables are set and settings pass validation:

```bash
bash infra/aws/docker/setup_ec2_docker.sh
```

This installs/builds dependencies, migrates the schema and starts **API + frontend only**.
No public demo users, historical alerts, camera feed or position feed are created. With a new
empty database, initialize private accounts, cars, devices, alert types and the trained model.
The planned SES trial needs real owner-recipient configuration: first follow
[the private owner-email staging procedure](../SES_TEST.md), before the first bootstrap.
No recipient address is supplied in this repository. Then, only in the authorized window:

```bash
bash infra/aws/docker/compose.sh exec -T api \
  python -m app.db.bootstrap --initialize-empty-database \
  --owner-emails-file /app/backend/.local_bootstrap/owner-emails.json
bash infra/aws/docker/compose.sh restart api
```

The restart reloads the newly registered active model: the API initially started with an empty
database and must refresh that model state before readiness/classification checks. The bootstrap
refuses a nonempty application database. It writes random account passwords and
device keys to `/app/backend/.local_bootstrap/credentials.json` (0600), on the API's persistent
`private-bootstrap` named volume. Container recreation retains it; do not remove that volume
with `down -v` or a volume prune. Export a private offline copy without printing credentials:

```bash
umask 077
install -d -m 700 "$HOME/.autosound-private"
bash infra/aws/docker/compose.sh cp \
  api:/app/backend/.local_bootstrap/credentials.json "$HOME/.autosound-private/credentials.json"
sudo chown "$(id -u):$(id -g)" "$HOME/.autosound-private/credentials.json"
chmod 600 "$HOME/.autosound-private/credentials.json"
```

Keep both the volume and the private backup until credentials are securely retained. Do not print
their contents in logs/screenshots, commit them, or reuse development passwords. If a bootstrap
reports an uncertain database commit, preserve the prepared credential bundle and inspect the
database privately before retrying; never delete the only credential copy. Existing data must
use an explicit migration/admin process; bootstrap never overwrites it. It creates no historical
MongoDB alerts. This project's demo separately requires the 25 historical alerts and original
audio: follow [the reviewed historical import](../HISTORICAL_IMPORT.md), verify all 25 and its
idempotent repeat, then enable the replay feed for five new results. Keep email subscriptions
disabled during bulk verification; [the SES check](../SES_TEST.md) is a separate bounded phase.

Keep the deployment variables in the current SSH shell. `docker/compose.sh` forwards them
explicitly through sudo for exec/cp/logs/ps/stop and the scoped `restart api` command. Use the
setup wrapper for builds/updates rather than direct `up --build`; other starts are refused.

## 5. Connect privately

On the laptop, keep this tunnel open:

```bash
ssh -N -o ExitOnForwardFailure=yes -i /private/path/key.pem \
  -L 127.0.0.1:5173:127.0.0.1:80 \
  -L 127.0.0.1:8000:127.0.0.1:8000 ec2-user@<ec2-address>
```

Open **http://127.0.0.1:5173**. Nginx forwards `/api/` to the API internally. External edge
clients on this laptop use `EDGE_API_URL=http://127.0.0.1:8000` through SSH and their private
registered device keys. Other machines need their own authorized tunnel; do not open API ports.
The backend binds the Docker network internally, but both published host ports bind 127.0.0.1.
Private S3 signed URLs still require internet connectivity in the browser and consume S3 usage.

## 6. Optional feeds and verification after authorization

Use private credentials from bootstrap (or F4 registration) in gitignored `infra/aws/edge.env`.
The supplied template contains placeholders, not working public keys. `EDGE_CLIENT=replay_client`
is the default: it replays bundled CARLA camera frames and telemetry with sound at the recorded
event, without a GPU or CARLA server. Set `EDGE_CLIENT=demo_client` for an illustrated virtual
car. Unknown selectors fail preflight; use the setup wrapper after a change, not a direct start.
Validate the private file locally:

```bash
python3 infra/aws/validate_production_env.py --edge-settings infra/aws/edge.env
```

Only after approving continuing request costs, rerun the wrapper with
`ALLOW_AWS_DEMO_FEEDS=I_UNDERSTAND_REQUEST_COSTS ENABLE_EDGE_DEMO=1`. In replay, choose a
scenario to start its recording; Start/Stop route resume/pause it. A new scenario replaces the
previous recording. Sound capture/ACK happens at its event; classification is asynchronous.
This is recorded footage, not a live simulated vehicle. The local recordings are copied into the
backend image, so rebuild locally after source changes before claiming verification.

Replay sends a camera PUT up to every second while frames advance, and resends a still frame
every five seconds when idle/paused: about $0.018/hour active or $0.0036/hour idle at the reviewed
S3 request rate, before other usage. The virtual alternative sends every two seconds ($0.009/hour).
See [planning assumptions](../README.md#planning-costs-before-credits); no AWS activity is
authorized under the current zero-credit budget. `ENABLE_DEMO_DATA=1`
separately requests `--demo-only` position history for untouched demo-tagged cars; real histories
and all alerts remain unchanged. It is off by default. Pass desired feed flags on every rerun;
feeds omitted on a later setup run are stopped. Stop edge-demo before a real CARLA client uses
those devices. A live CARLA server's machine, map, graphics and hosting are a separate decision;
recorded replay requires none of those resources.

Using private logins, check F4/F5 Wi-Fi/device commands, F11 edge status, F13 upload/process/audio,
C4 classification/alerts and F14 storage/backup. A successful HTTP request is not proof of physical
Wi-Fi changes or a real hardware reboot. Any real-store readiness call, upload, email, backup,
automated live verifier or continuously running feed consumes AWS usage; none is authorized
under the current zero-credit budget. Local production tests do not prove these AWS integrations.

## 7. Updates and cleanup after authorization

Record the current commit before update. Retain the private bootstrap volume and its offline
backup across recreation. Pull/review the desired commit, retain private settings, then rerun
the wrapper with the same consent and desired feed flags. Database migrations are forward-only;
checking out old code is not an automatic database rollback. Model activation/rollback belongs
to F8b after the artifact and model row have been verified.

Stopping EC2/RDS leaves billed disks, EIP, S3 and backups; RDS restarts after seven days. To end
usage, separately authorize and review teardown: stop feeds, export needed evidence privately,
terminate the owned EC2 instance, release its EIP, delete owned RDS and unwanted retained
snapshots, empty/delete the owned app bucket and remove the Atlas deployment if no longer
needed. Retained snapshots/objects continue to cost. Never bulk-delete unrelated resources.
