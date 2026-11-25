# AWS deployment safety and cost review

Author: Rishi Sheth. Current authorized AWS credit budget: **$0**. Local review and tests are
allowed; creating resources, installing on an AWS instance, starting feeds, probing AWS stores,
and uploading audio are deferred until separate authorization. No AWS deployment is claimed.

**Option B is the recommended private demo:** [Docker setup](docker/SETUP.md). Its API and UI
bind EC2 loopback and are reached through SSH. [Option A](SETUP.md) runs the API with systemd
and the frontend locally through the same kind of tunnel. The former public HTTP S3 website
publisher is disabled. Public hosting requires a separate HTTPS, authentication and cost review.

## Deployment guard

All AWS setup/deploy shell entry points check consent before package downloads, local/server
mutations, migrations or restarts. With missing consent or `AWS_CREDIT_BUDGET_USD=0`, they exit.
For a later authorized deployment, these three variables are required together:

```bash
export ALLOW_AWS_DEPLOYMENT=I_ACCEPT_AWS_RESOURCE_CHANGES
export AWS_COST_ACK=I_ACCEPT_AWS_USAGE_COSTS
export AWS_CREDIT_BUDGET_USD='<separately-approved-positive-planning-amount>'
```

The amount is an acknowledgement, **not a hard spending cap**. Do not populate it while the
budget is zero. Do not bypass the wrapper with direct Compose build/up, installer commands,
or AWS console/CLI changes. Budget notifications can arrive after usage; they do not stop costs.
See [AWS Budgets timing](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html).

Defaults are no development seed and no feeds. `SKIP_SEED=0` is refused in production. A new
empty database uses private bootstrap credentials, not public repository passwords/device keys.
Optional feeds require `ALLOW_AWS_DEMO_FEEDS=I_UNDERSTAND_REQUEST_COSTS` and explicit
`ENABLE_EDGE_DEMO=1` or `ENABLE_DEMO_DATA=1` on each setup run. Omitted feeds are stopped on
subsequent setup runs. For the edge feed, `EDGE_CLIENT=replay_client` is the default in both
the native systemd service and AWS Docker option; set `EDGE_CLIENT=demo_client` for the
illustrated virtual car. The shared preflight rejects other selectors. Replay uses bundled CARLA
camera/telemetry recordings and needs no GPU or CARLA server. It does not operate a live CARLA
world: choose a scenario to begin playback, then Start/Stop route resume/pause that recording.
The position feed also requires `--demo-only` and preserves real history
and all alerts; it is a demonstration of untouched demo cars, not live CARLA data.

## Account plan and resource sizing

Both guides use **us-west-2**, a Linux **x86_64** EC2 instance, private **Single-AZ RDS
PostgreSQL 16 db.t4g.micro with 20 GiB gp2/gp3**, MongoDB Atlas M0, private S3 Standard app
storage and SES. Neither requires an ALB, NAT gateway, CloudFront distribution, public RDS,
or a public UI bucket. Atlas is an external provider; its account eligibility/limits are separate.
The Docker guide still uses managed RDS and Atlas; local PostgreSQL/MongoDB would be a
separate, unverified architecture change.

The old `t3.medium` choice is **not listed in the current EC2 Free-plan eligible types**.
`c7i-flex.large` (2 vCPU, 4 GiB, x86) is listed and is the planning candidate in these guides;
`m7i-flex.large` (2 vCPU, 8 GiB) is another listed candidate if measured memory requires it.
Neither size has been deployed or capacity-tested for this AWS setup. Eligibility, AMI, regional
availability, quota and current plan must all be checked before authorizing a launch; an eligible
label means credits may fund usage, not zero credit consumption. ARM requires a different build.
[Current EC2 eligibility](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html)

An active Free account plan ends after six months or credit depletion, whichever comes first;
access is suspended/closed and data is retained for 90 days. Staying Free is different from
upgrading to Paid. Do not activate advanced features, Control Tower or enroll in Organizations
to create a guardrail: these can require/trigger Paid and remove protections. A managed
organization supplied by the new signup experience is a separate default. Actual account credit
expiry and eligibility take precedence over old generic “750 hours / 12 months” claims.
[RDS current and legacy offers](https://aws.amazon.com/rds/free/),
[Account plan rules](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html),
[Advanced features](https://docs.aws.amazon.com/accounts/latest/reference/activate-advanced-features.html)

## Planning costs before credits

Public Oregon rates reviewed November 23, 2026. USD, on demand, no tax, reservations,
CPU surplus, extra backups, traffic or optional services. A planning month is 730 hours;
exactly 30 days is 720 hours. Storage is prorated by its actual billing duration.

| Resource | Rate |
|---|---:|
| Original t3.medium, hypothetical only | $0.0416/hour |
| Free-plan listed c7i-flex.large, 4 GiB candidate | $0.08479/hour |
| Free-plan listed m7i-flex.large, 8 GiB candidate | $0.09576/hour |
| EC2 gp3, baseline performance | $0.08/GiB-month |
| One public IPv4 or retained Elastic IP | $0.005/hour |
| RDS db.t4g.micro Single-AZ | $0.016/hour |
| RDS gp2/gp3, 20 GiB | $2.30/month |
| S3 Standard, first tier | $0.023/GB-month |
| S3 PUT/COPY/POST/LIST | $0.005/1,000 requests |
| S3 GET/HEAD | $0.0004/1,000 requests |

Formula: `hours × (EC2 + .016 RDS + .005 IPv4) + hours/730 × (EBS + 2.30 RDS storage)`.

| c7i-flex.large candidate | 1 hour | 8 hours | 24 hours | 730 hours |
|---|---:|---:|---:|---:|
| A, 20 GiB EC2 disk, fixed resources | $0.111 | $0.889 | $2.667 | $81.127 |
| B, 30 GiB EC2 disk, fixed resources | $0.112 | $0.898 | $2.693 | $81.927 |
| Replay camera idle/paused, every 5 seconds, PUT only | $0.0036 | $0.0288 | $0.0864 | $2.628 |
| Replay camera advancing, up to once per second, PUT only | $0.018 | $0.144 | $0.432 | $13.140 |
| Virtual demo camera, every 2 seconds, PUT only | $0.009 | $0.072 | $0.216 | $6.570 |

At the reviewed request rate, B with one replay camera is approximately **$84.555/month idle**
or **$95.067/month continuously advancing**, before other usage. These are planning bounds,
not a measured bill: frame changes, playback duty cycle, retries and camera capture state matter.
The virtual demo alternative remains about **$88.497/month** with its two-second camera.
The original t3.medium A/B figures would be $49.598/$50.398 fixed, or $56.168/$56.968 with
that virtual camera, but are not a Free-plan deployment recommendation. The 8 GiB m7i candidate adds
$0.01097/hour (about $8.008/month) over c7i. S3 audio and backups grow with use; overwriting
`latest.jpg` avoids accumulated frames only when bucket versioning is off. Retries, HEAD/GET
polling and viewers add requests. One viewer fetching every two seconds adds about $0.526/month
in GETs, plus transfer. Replay heartbeats run every second; the UI also polls every second for
45 seconds after a scenario click. Idle replay resends its still frame every five seconds, so a
two-second cost assumption must not be applied to all clients. No feed or camera/request activity
is enabled automatically by these AWS installers, and no AWS usage is authorized at budget zero.

First 100 GB/month internet egress is aggregated across AWS services/regions (not per bucket);
then the first Oregon paid tier is $0.09/GB. Inbound internet transfer is free, but uploads still
have request/storage costs. Same-region S3 to AWS-service transfers have no transfer fee.
New SES account/region combinations default to Essentials at $0.16/1,000 recipient emails;
à-la-carte is $0.10/1,000, with $0.12/GB outbound data. Extra SES features are not included.
Do not assume an old SES free allowance. T3 surplus CPU adds $0.05/vCPU-hour; RDS T4g surplus
adds $0.075/vCPU-hour. Cross-AZ traffic, snapshots, logs and optional features can add costs.

**Stopping is not teardown.** Retained IPv4 + EC2 disk + RDS storage are about **$7.55/month A**
or **$8.35/month B**, before S3/backups, even with compute stopped. RDS automatically restarts
after seven days, so this is not a month-long paused forecast. RDS excess snapshots/backups are
$0.095/GB-month; deleting the database while retaining snapshots does not remove all costs.
[RDS stopping](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html)

Sources: [Regional EC2 public price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-west-2/index.csv),
[EBS price data](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ebs.json),
[RDS price data](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/rds/USD/current/rds-postgresql-ondemand.json),
[S3 price data](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/s3/USD/current/s3-standard.json),
[IPv4](https://aws.amazon.com/vpc/pricing/), [S3 transfer](https://aws.amazon.com/s3/pricing/),
[SES](https://aws.amazon.com/ses/pricing/).

## Supplied historical audio

The project [shared Drive folder](https://drive.google.com/drive/folders/1XvLLGLWz2NwelPaCrykmZKbfcL5IvLtQ)
was listed read-only: 30 WAVs, about 216 MB. Filenames cover all 25 object keys in
`backend/app/db/seed_data/alerts.json`. Listing establishes filename coverage, not playable
contents or successful uploads. No audio was fetched or uploaded for this AWS review.

The private bootstrap does not create historical MongoDB alerts. Newly recorded scenarios
upload their own audio after separately authorized deployment. Historical seed migration is
optional; do not invent alert rows or upload arbitrary filenames to make the UI look populated.
If these historical alerts are deliberately imported later, first obtain the WAVs locally through
the supplied folder, then create a manifest outside git (the parent output directory must exist):

```bash
python3 infra/aws/seed_audio_manifest.py --directory /local/path/to/WAVs \
  --output /private/path/seed-audio-manifest.json
python3 infra/aws/seed_audio_manifest.py --verify /private/path/seed-audio-manifest.json
```

This helper performs no network calls. It requires exactly one valid local WAV for each tracked
key, rejects ambiguous/missing/truncated files and symlinks, records size/SHA-256, and rechecks
hashes. Extra folder WAVs are not assigned upload keys. A manifest is not upload authorization;
a later reviewed uploader must revalidate it and target the chosen private bucket. Do not use
the old unvalidated recursive “upload every WAV” snippet or trust `.local_audio` as automatically
present. Keep paths/manifests/audio/private credentials out of commits.

## Local checks

```bash
python3 -m unittest discover -s infra/aws/tests -v
python3 infra/aws/validate_production_env.py /private/path/backend.env
bash -n infra/aws/setup_ec2.sh infra/aws/deploy_backend.sh infra/aws/deploy_frontend.sh \
  infra/aws/docker/setup_ec2_docker.sh infra/aws/deployment_guard.sh
```

These checks need no AWS credentials or services. They do not prove an AWS deployment, delivery
of SES mail, regional launch permission, or external CARLA compatibility.

### Repeatable local application verification

[`scripts/verify_local.py`](../../scripts/verify_local.py) starts fresh local PostgreSQL 16,
MongoDB 7, MinIO, the API/model/workers, the virtual edge client and a built frontend in a Linux
network namespace containing only loopback. It refuses to run without isolation, installs
nothing, excludes private settings/data from its source snapshot and removes its temporary
stores and processes afterward. Run `python3 scripts/verify_local.py --help` for the required
paths to already installed tools and an empty evidence directory.

The November 23, 2026 local run passed **609 unit/model tests**, then all **10 PostgreSQL
migration tests** separately, private bootstrap/repeat-refusal checks, C1–C5 live API checks,
all five virtual-edge scenarios and Chromium audio/UI checks. An actual local MinIO outage
produced readiness 503 while PostgreSQL health remained 200; recovery returned readiness 200.
The separate AWS safety suite passed **15 tests**, and Compose configuration parsed with dummy
settings without starting containers. Evidence includes JSON, JUnit XML, logs and screenshots.

That native run used Python 3.14; its email and backup dumps were simulated, while inference
and MinIO storage were real. The separate production-image checks below cover the pinned
Python/Torch environment and actual database dumps/restores.

### Production image and real restore verification

The November 23, 2026 production image build with `INSTALL_BACKUP_TOOLS=true` passed locally.
The verified backend image `autosound-production:local-nov23` has ID
`sha256:5328f519d401ce329123b06e46899ac1f7743fab31bf14c03deb3a931634abd4`.
It ran **Python 3.12.16, torch/torchaudio 2.5.1+cpu, PostgreSQL tools 16.16, and MongoDB tools
100.20.1**. The MongoDB archive is pinned by its official SHA-256; the remaining package versions
are recorded with the image evidence rather than claimed to be fully locked. Inside this exact
image, `pip check` passed and **614 backend tests passed, with 10 PostgreSQL integration tests
skipped and zero failures/errors**, under `--network=none` without private settings mounted.

The final live local run used the built backend/frontend images and fresh PostgreSQL, MongoDB
and MinIO on an internal Docker network without published ports, an external route or external
DNS. Nginx assets, SPA fallback, API proxy, private JWT login and authenticated readiness passed.
C2/C3 device configuration, command delivery, upload validation/idempotency, real model/worker
inference and byte-identical signed audio passed on both the source and restored databases.

With **`DEV_FAKE_BACKUP=false`**, the HTTP backup job created genuine PostgreSQL custom and
gzip MongoDB archives in MinIO. Downloaded SHA-256 hashes matched; actual `pg_restore` and
`mongorestore` into fresh databases passed comparisons across **9 SQL tables and 5 MongoDB
collections**, including rows/BSON, indexes, collection options, SQL foreign keys, uniqueness and
sequence state. Four private logins, the original device key, encrypted Wi-Fi data and the active
model remained usable after restore. PostgreSQL's equivalent CHECK-expression deparse was
compared against a third database loaded independently from the complete source schema; no
constraints were omitted. Only the backup job's expected post-dump lifecycle changes
(`status`, `result`, `finished_at`) were normalized in row comparisons.

The final synthetic Docker-context test also passed: the real ignore rules excluded `.env`,
bootstrap credentials, edge settings and nested local evidence canaries while copying safe
source. No real secrets were used. Owned test containers, anonymous volumes, internal network
and the temporary context-test image were removed; existing resources were not pruned.
The evidence bundle is `production-docker-local-nov23/`, specifically
`verification-final/verification.json`, `image-unit/` (JUnit, logs, versions) and
`context-safety.json`.

The November 24 upstream update adds recorded CARLA replay and new audio fixtures; the
November 23 image IDs and test counts above describe the earlier source snapshot, not proof of
that update. Replay needs no GPU/server; live CARLA remains a separate integration.

### Replay behavior and remaining limits

The Simulator labels this feed **recorded CARLA replay**. A scenario starts its recording;
Start route resumes a paused recording and rejects idle/finished playback. Stop route freezes
its timeline. Stopping microphone capture cancels an outstanding sound event; starting capture
again does not revive the cancelled command. Already captured FIFO audio remains queued.

The scenario ACK confirms a durably buffered clip, not completed upload or model processing.
F13 and the generated alert establish that later result. The client keeps a private command
journal in its buffer volume: an ordinary restart reports an interrupted pre-event replay as
failed, or retries a saved outcome without replaying audio. The API still delivers commands
once, so a crash between server delivery and the first journal write can leave that command
waiting until expiry. Server redelivery/receipt handling remains a hardening follow-up. File
fsync/atomic replacement is not a claim of full power-loss recovery. Audio follows heartbeat
and browser polling, so synchronization is approximate rather than frame exact.

The three course-provided sound clips have no original license metadata in the supplied
manifest. Their source is identified; ESC-50's collection license is applied only to the two
ESC-50 clips. The five expected predictions are fixture regressions, not a population accuracy
measurement or independent verification of the dataset split.

### November 24 verification of the replay integration

The safety work was rebased onto `main` at `d42a529` (including PR #9) on review branch
`fix/aws-local-safety-replay`. The four guide/environment conflicts retain the private
deployment controls, with `EDGE_CLIENT=replay_client` added. Both documented position-feed
commands require `--demo-only`. All **18 offline infrastructure tests passed**, including a
stubbed x86 target for the Docker control-flow test, explicit ARM-target rejection and invalid
edge-selector rejection before external commands.

The rebuilt backend image `autosound-production:local-nov24` is
`sha256:6ce26a9a22ee762a4d7439272e0841fa7d07c80bcadf99c481a8a57c129f6f6e`.
Inside that exact image, **646 backend tests passed, 10 PostgreSQL tests skipped, zero failures**;
`pip check` passed with networking disabled. All **10 PostgreSQL migration tests passed** in a
separate disposable native cluster. The backend suite includes 20 replay lifecycle/recording
tests and five real exported-model fixture predictions.

The final frontend image is
`sha256:1e85dc31621707dce52f16e73daae5c12cf066dc3f263a8acc9e44f0199fe9bc`.
A fresh isolated application run passed C1–C5 API checks, C2/C3 browser checks, all five real
recorded-replay commands/uploads/predictions, matching stored WAV hashes, changing recorded
camera/GPS, event-delayed audible previews, pause/resume and Cloud read-only monitoring.
Separate browser fault injections passed queue rejection, failed/expired commands, autoplay
recovery and download retry without early playback. This caught and fixed CSS overriding an
audio player's `hidden` attribute. The final UI run used `--application-only`; its JSON explicitly
records omitted unit/migration suites and must be read alongside their separate passing
evidence. The unchanged backend suites were not repeated for that CSS correction.

`verify_production_local.py` also passed against both final images: all five actual replay
scenarios used private bootstrap device keys, the production model and local MinIO; uploaded
and signed-download audio hashes matched. Replay stopped before real database backups.
Genuine PostgreSQL/MongoDB dumps, actual restores, full schema/data/index/sequence/BSON
comparisons and restored private logins/device keys/Wi-Fi/model inference all passed. All
owned test processes, stores, containers, volumes and internal networks were cleaned up.

The local evidence bundle is `cmpe281-replay-local-nov24/`: `application-final/verification.json`,
`application-final/replay/replay-browser-verification.json` and screenshots,
`image-unit/verification.json`/JUnit, `native-final/postgres-migrations.xml`,
`production-final/verification.json`, and `infra-tests.log`. Earlier failing runs are retained;
use the final paths above for the corrected result. No AWS deployment, managed-service
requests, SES delivery or credit use was performed.

**Remaining:** live CARLA if required; AWS instance capacity, IAM/network/S3/SES delivery
and public TLS after separate authorization. Email stayed logged locally. These are small local
fixtures, not an AWS load/capacity test or a model training/accuracy evaluation. Backups are
sequential logical database dumps taken with application writes quiescent, **not an atomic
cross-database point-in-time snapshot**. They do not back up S3 media payloads, private settings,
encryption keys or database-server roles; those need a separate recovery plan. No AWS account,
resource, credit or charge was used for these local checks.
