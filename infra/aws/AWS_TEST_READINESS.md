# AWS trial readiness and credit planning

Author: Rishi Sheth. Reviewed baseline: `cd5b022`, now on main. **Review only; authorized AWS usage and credit
consumption remain $0.** This is a proposal, not permission to create resources, probe stores,
send email or deploy. No account balance, identifier, recipient or credentials are recorded.

## What is already proved

The final November 24 evidence is in `outputs/cmpe281-replay-local-nov24/`:

- `image-unit/`: **646 backend tests passed, 10 PostgreSQL tests skipped** inside the production
  image without networking; all **10 migration tests passed** separately. Infrastructure: **18 passed**.
- `application-final/verification.json`: fresh browser/application proof, including five scenarios;
  explicitly application-only, to be read with the separate unit/migration evidence.
- `production-final/verification.json`: final nginx/private JWT, five real replay uploads/model
  results, matching audio hashes, changing recorded camera/GPS and genuine database restores.
  Owned test resources were removed.

These establish local production-image behavior, not a working AWS deployment. Replay uses
bundled recorded CARLA footage and requires neither a GPU nor a live CARLA server.

Shareable, sanitized evidence is committed with this packet:
[machine-readable results](evidence/local-nov24.json) and
[the recorded-replay UI screenshot](evidence/recorded-replay-nov24.png).
They contain synthetic application fixtures only; no AWS account details, balances, email
addresses, private credentials or signed URLs. Full local logs/JUnit remain in the evidence bundle.

Active Free-plan status and credit availability were rechecked read-only on November 24.
No resource inventory was queried. Recheck plan, expiry, eligible credits and permissions
immediately before launch; actual cloud configuration remains unverified.

| Area | Proven locally | Still required for an AWS trial |
|---|---|---|
| Build/capacity | Python 3.12, CPU Torch 2.5.1, images/tools execute | Oregon AMI/type eligibility, quota, capacity and measured host memory/disk |
| Private access | Secret validation, bootstrap, JWT roles, loopback ports, guards | Actual private settings, secret retention, SSH-only group, tunnel/CORS, instance role |
| Databases | Real PostgreSQL 16/MongoDB 7 migrations and restores | RDS TLS/restricted-user privileges; Atlas M0 availability, TLS, permissions/allowlist |
| S3/IAM | MinIO writes, signed bytes and archives | AWS bucket region/privacy and required prefix/list/read/write permissions |
| C2/C3/C4 | Commands, five replay captures and real inference | Same flow through actual RDS, Atlas and AWS S3, including failures/capacity |
| C5 | Full local schema/data/BSON/index/sequence comparisons | Managed-store dump permissions/TLS and S3 transfers; restore locally, not into another cloud DB |
| SES | Logged only | Region, verified identities, sandbox/IAM and received mail; budget emails are separate |
| Plan/credits | Read-only account check; dated estimate | Fresh eligibility/expiry/allowance checks; no account-specific values belong in this packet |

The actual private settings and AWS integrations remain unverified. If a service/type or Atlas
free tier is unavailable, stop and review; do not upgrade to Paid or add paid services as a workaround.

## One bounded two hour trial

After separate credit authorization, propose **Option B alone**: Oregon, one x86 `c7i-flex.large`,
30 GiB gp3, one IPv4, private Single-AZ RDS `db.t4g.micro`/20 GiB, available Atlas M0, private
S3 Standard and basic SES. UI/API use SSH. No GPU, ALB, NAT, public UI bucket or second cloud
DB. Keep [guards/private bootstrap](README.md#deployment-guard); no live commands are supplied here.

| Trial time | Work and exit condition |
|---|---|
| 0–30 minutes | Timer starts at first billable resource. Startup/private settings/bootstrap/tunnel. If readiness or eligibility fails by minute 30, abort into teardown. |
| 30–40 minutes | Real role/store checks and readiness; import the reviewed 25 historical alerts/audio, then check idempotence and signed downloads. |
| 40–60 minutes | C1–C5 role/UI walkthrough and five new replay results; C2 config/commands and C3 idempotency/processing/audio. Record the actual trial with audible clips, one camera/browser and sanitized evidence. |
| 60–75 minutes | Quiesce replay, one real backup, download/verify archives and restore into disposable local stores only. |
| 75–90 minutes | At most one verified-recipient SES message, confirm receipt, finish role/failure checks and evidence. Stop feeds. |
| 90–120 minutes | Remove owned resources, including IP/disks/database/snapshots/objects; handle Atlas separately. Verify cleanup; retain nothing without separate approval. |

Two hours is a stop target, **not a billing cap**: delays/retained resources cost extra. Stay until
cleanup is confirmed. Stopping EC2/RDS retains costs; RDS can restart after seven days. Logical
dumps exclude media/secrets/server roles and are not atomic across databases; retain a private recovery plan.

Anusha confirmed that the 25 historical alerts with playable audio are required in addition to
five new replay results. Their original WAVs have been downloaded and validated locally;
see [the safe historical import](HISTORICAL_IMPORT.md). Include their reviewed import and
signed-audio checks in the trial. No AWS upload has been performed.

## Credit estimate for review

Use **November 23, 2026 [README rates](README.md#planning-costs-before-credits), not fresh quotes**:
USD credit-equivalent usage before conditional benefits. Assumes resources last only the stated
duration. Recheck billing/proration, eligibility and allowances before approval.

For Option B, with `t` hours:

`fixed(t) = t × (0.08479 EC2 + 0.016 RDS + 0.005 IPv4 + (30 × 0.08 + 20 × 0.115) / 730)`

| Illustrative duration | Fixed resources | Camera PUT bound at 1/second | Subtotal before other usage |
|---|---:|---:|---:|
| 1 hour | $0.1122 | $0.0180 | $0.1302 |
| 2 hours | $0.2245 | $0.0360 | $0.2605 |
| 4 hours | $0.4489 | $0.0720 | $0.5209 |

Camera assumes continuous advancement; idle sends every five seconds, and this plan enables
the feed only during validation. The 1/4-hour rows are comparisons, not approved plans. An approved
8 GiB `m7i-flex.large` alternative adds the dated $0.01097/hour; never change size automatically.

For the two-hour trial, use these explicit additional planning bounds:

- **1,000 other PUT/LIST** and **20,000 GET/HEAD**: **$0.013**.
  `requests = 0.000005 × other_put_list + 0.0000004 × get_head`.
- **1 GB average S3 data**: **$0.000063**; retention/versioning is extra.
- **3 GB internet egress**: budget **$0.27**, without assuming unused shared free transfer.
  Three complete historical verification phases alone stream about 1.92 GB; media may be long.
- **One small SES email**, no attachments/add-ons: about **$0.0002**. Confirm Atlas M0 remains free.

With full historical-media checks, that is roughly **$0.54 before unmodeled items**. RDS CPU surplus, cross-AZ traffic, logs,
snapshots, retries, delays, retention and other account usage add cost. Propose **$1 credit per
two-hour trial plus a reserve** as contingency planning, not a cap/guarantee. It authorizes
nothing under the current $0 instruction.

For repeated trials, let `C` be currently available eligible credits, `R` a protected reserve and
`A` the reviewed per-trial planning allowance:

`planning_count = max(0, floor((C − R) / A))`

| Hypothetical credits, not an account balance | Illustrative 20% reserve | $1 allowances remaining |
|---|---:|---:|
| $10 | $2 | 8 two-hour trials |
| $20 | $4 | 16 two-hour trials |
| $50 | $10 | 40 two-hour trials |

These are **not guaranteed test counts**. A trial includes startup/validation/teardown, not one
unit test; local regressions consume no AWS credits. Expiry, restrictions, uncovered charges and
runtime/other usage reduce capacity. Credits do not extend the Free-plan period or make a cloud
trial consume zero credits. Reconcile actual usage and cleanup before approving another trial.

## Written decisions confirmed by Anusha

Anusha reviewed `cd5b022` and approved its merge; that exact baseline is now on main. She
confirmed Option B with the private SSH tunnel, fresh AWS/Atlas resources and the 4 GB
`c7i-flex.large` candidate with one API worker. This is her expected sizing, not a measured
cloud-capacity result. The 25 historical alerts/audio are required in addition to five new
replay alerts. SES sender/recipient verification is not complete; the intended recipient will
be provided privately later. Her proposed sequence is a two-hour trial with full cleanup,
then a fresh deployment shortly before the demo, removed afterward. The demo date and
later deployment duration are not supplied. Neither event has an authorized credit allowance.

Before either future cloud window, the owner still needs the intended private SES identities,
the actual demo date/duration, and a reviewed credit allowance for that window. Real cloud
resource settings, permissions and connections are established and verified only during the
authorized trial. Keep all credentials and account identifiers out of group text and evidence.

There is now a private `--owner-emails-file` bootstrap option: notification recipients come
from the actual car owner's database address, not `SES_FROM_EMAIL`. Configure that file
before the empty-database bootstrap and keep email subscriptions off during bulk checks.
[SES_TEST.md](SES_TEST.md) describes the separate one-event delivery procedure. No identities
have been verified and no real email has been sent by this preparation.

The account owner chooses the credit allowance after these decisions; a teammate's deployment
request does not authorize credit use. Current instruction prohibits all further AWS actions,
including read-only console/API work. This packet is preparation only.

## Evidence to capture during a later authorized trial

| Area | Pass evidence |
|---|---|
| Revision and private access | Tested commit recorded; ports restricted to SSH/tunnel; production preflight passes without printing secrets; private bootstrap remains private |
| Store/worker readiness | Public health 200 and authorized Cloud Staff readiness 200 with actual RDS, Atlas, S3, loaded model and live workers; expected 401/403 for other roles |
| C1 and access control | Correct owner-scoped dashboard/map/tracking/subscription behavior with current positions; cross-owner and staff-only access remains denied |
| C2 | Device registration and Wi-Fi configuration/command ACKs work; five recorded scenarios advance frames/GPS; capture cancellation and pause/resume behave as documented |
| C3/C4 | Every accepted clip has one processed record and linked deployed-model alert; signed audio bytes match the input; selected scenario and model result are distinguishable |
| C5 and recovery | Real archive downloads have stable hashes and restore into fresh local stores; original cloud source preserved; active model/configuration and admin role checks pass |
| SES | One intended verified recipient receives the expected alert/test email; identity, role and sandbox configuration confirmed without storing addresses in public evidence |
| Completion | Sanitized UI screenshots/results; trial-owned resources removed and cleanup confirmed; actual credit usage reconciled when billing catches up before another trial |

The historical follow-up is implemented locally on the review branch; its evidence and rehearsal
are documented in [HISTORICAL_IMPORT.md](HISTORICAL_IMPORT.md).

Its production-image checkpoint passed 724 backend tests, 10 separate PostgreSQL migration
tests, 18 infrastructure tests, the complete 25-alert/byte-identical-audio import and five new
replay results, plus real backup/restore. The full local walkthrough video remains uncompleted;
only recorder privacy/audio smoke checks passed. A real AWS/tunnel recording belongs to the
later authorized trial, not to this local evidence.

Ready for a controlled AWS attempt means local evidence is green, the written configuration
choices are settled, the branch/revision is agreed, and the private configuration prerequisites
are available. It is not proof that real AWS connectivity works; the authorized trial establishes
that. Resource creation, chargeable probes and deployments still require the account owner's
explicit approval of the reviewed credit allowance. No deployment is authorized now.

## Primary references

- [AWS Free versus Paid account plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html): staying on an active Free plan protects against cash charges; service usage can still consume credits.
- [Current EC2 Free-plan instance eligibility](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html): includes the two x86 Flex candidates, not the original `t3.medium`.
- [AWS Budgets behavior](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html): notifications can lag usage and do not make the planning allowance a hard billing cap.
- [Public IPv4 pricing](https://aws.amazon.com/vpc/pricing/), [RDS PostgreSQL pricing](https://aws.amazon.com/rds/postgresql/pricing/) and [EBS pricing](https://aws.amazon.com/ebs/pricing/): recheck exact regional/configuration rates before any separately approved trial.
