# Historical alerts and original audio

Author: Rishi Sheth. **Local preparation only. AWS actions, deployment and credit use remain
unauthorized.** The commands describing a cloud import are a later runbook, not permission to
execute them.

Anusha confirmed that the demo needs the original 25 historical alerts with playable audio,
followed by five new replay-scenario results. The private bootstrap still creates the users,
cars, devices and model; this importer never creates public development passwords or keys.

## Source and behavior

- The 25 fixture identities come from `backend/app/db/seed_data/alerts.json`. Only their
  matching WAVs are selected from the shared course folder; unrelated files are excluded.
- All 25 downloaded WAVs passed full payload/header/key/SHA-256 checks. They total
  **213,006,796 bytes**. Five files are approximately 41 MB and 1,285 seconds each; their
  complete original bytes are retained. The others are approximately 1–32 seconds long.
- The importer maps each fixture to the actual bootstrap car's plate/make/model and its
  private microphone. It preserves historical timestamps, IDs and classifications, creates
  linked processed F13 records, and remaps object keys to the actual car IDs.
- Historical classifications are supplied fixtures, **not new model predictions**. Typed
  provenance and UI notices distinguish these from the five newly processed replay records.
  No historical clip is reclassified and no historical alert email is sent.
- Local files, SQL mapping, existing Mongo identities and existing object bytes are checked
  before writes. Existing data is never replaced. Conditional object creation and unique
  identities support retries after partial progress; the operation is not cross-store atomic.
- The WAVs and path-bearing review manifest stay outside Git. No account details, addresses,
  private bootstrap bundle, environment file or credentials belong in shared evidence.

## File-only review

These commands access local files only, with no database or cloud requests:

```bash
python3 infra/aws/seed_audio_manifest.py --directory /private/course-wavs \
  --output /private/historical-review.json
python3 infra/aws/seed_audio_manifest.py --verify /private/historical-review.json
cd backend
python -m app.db.import_history --audio-directory /private/course-wavs \
  --manifest /private/historical-review.json
```

The last command defaults to validation with zero writes. Applying requires the explicit
reviewed manifest and either `--apply-local` or `--apply-cloud`. Local mode requires direct
local database/object-store targets and explicit local credentials. Cloud mode requires
production settings and the existing exact deployment/cost acknowledgements with a positive
approved credit budget **before any SDK client or database access**. A zero budget blocks it.

## Later authorized Docker import

After a separately approved cloud setup and private bootstrap, restart the API to load its
active model. Stage only the validated WAVs and a key/size/hash manifest in an application-user
owned private container directory. The reviewed manifest can omit local `file` paths: apply
compares its 25 keys/sizes/hashes against the specified current audio directory.

For copying with Docker, keep the destination directory owned by `autosound` and mode 0700;
copy only its contents into that existing directory. Staged course WAVs and the path-free
manifest must be readable by the application user. Do not make bootstrap or environment files
readable to other users or copy them with the audio. Check permissions before applying.

The application command is:

```bash
python -m app.db.import_history --audio-directory /private/staged-wavs \
  --manifest /private/staged-review.json --apply-cloud
```

When invoking it through guarded `compose.sh exec`, explicitly pass the three already
approved acknowledgement variables with `exec -e ALLOW_AWS_DEPLOYMENT -e AWS_COST_ACK
-e AWS_CREDIT_BUDGET_USD`; Docker does not inherit arbitrary client environment variables.
Never run the development seed in production. Enable the replay feed only after the 25-alert
baseline has been checked. A repeated import should insert zero objects, records and alerts.

## Local evidence and recording

`scripts/verify_local.py --historical-audio-dir ... --record-demo` keeps private bootstrap
accounts and verifies all 25 linked alerts/records, staff and owner permissions, and exact
signed audio bytes. It then checks five distinct new replay records and runs a local rehearsal
recording. `scripts/verify_production_local.py --historical-audio-dir ...` repeats historical
checks against the production images before/after replay and after real database restores.

One complete historical verification phase performs 75 authenticated audio downloads
(Cloud Staff, IoT Staff and the correct owner), streaming **639,020,388 bytes** with bounded
memory. Three phases stream about 1.92 GB; allow for this in later cloud transfer planning.

The rehearsal shows C1–C5, short historical samples and all five audible replay events. All 25
files are independently verified; the video does not claim to play all five 21-minute clips.
The persistent local-rehearsal label identifies the environment. Authentication and sensitive
dialogs are covered before recording; only selected application audio is captured, with no
desktop or microphone capture. The actual AWS trial/tunnel video remains a later task after
explicit authorization. SES delivery remains untested until that authorized setup.
