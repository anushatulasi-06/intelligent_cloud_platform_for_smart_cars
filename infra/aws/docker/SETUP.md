# Setup: Option B (Docker on EC2)

This guide covers the complete AWS setup for all five components (C1–C5), from an empty account
to a working, checked deployment, plus updates, rollback and teardown.

| Container | Port | What it is |
|---|---|---|
| `api` | 8000 | C1–C5 API, C3 processing worker, C2 device sweeper, C4 model |
| `frontend` | 80 | nginx serving the UI and forwarding `/api/` to the API |
| `edge-demo`, `demo-data` | — | optional demo feeds |

| Service | Holds |
|---|---|
| **RDS PostgreSQL** | users, cars, devices, commands, config, models, jobs |
| **MongoDB Atlas** | alerts, audio records, positions |
| **S3** | audio clips, camera frames, backups, archives |
| **SES** | sends alert emails |

There is no load balancer. For the script-based alternative with the frontend on S3, see
[`../SETUP.md`](../SETUP.md).

**When you're done you'll have:**
- UI: `http://<elastic-ip>/`
- API: `http://<elastic-ip>:8000` (docs at `/docs`)

**Time:** about 2 hours the first time. **Cost:** roughly $30–45/month while running (see
[step 11.5](#115-pause-resume-and-cost)).

---

## Values you'll collect

Fill these in as you go. Step 6 uses all of them.

| Value | From step | Example |
|---|---|---|
| `<region>` | 1 | `us-west-2` |
| `<data-bucket>` | 2.1 | `autosound-data-group8` |
| `<elastic-ip>` | 2.3 | `54.12.34.56` |
| `<key>.pem` | 2.3 | `autosound-key.pem` |
| `<rds-endpoint>`, `<rds-password>` | 2.4 | `autosound-db.abc123.us-west-2.rds.amazonaws.com` |
| `<MONGO_URI>` | 2.5 | `mongodb+srv://app:...@cluster0.xxxx.mongodb.net/autosound?...` |
| `<sender-email>` | 2.6 | `alerts@yourdomain.com` |

---

## 1. Before you start

**Accounts:**
- An AWS account with console access. Use one region for everything; this guide uses `us-west-2`.
- A MongoDB Atlas account (free) at cloud.mongodb.com.

**On your laptop:** only `git` and an SSH client (included with macOS). The images are built on
the instance, so neither Docker nor Node is needed locally.

**Code:** merge everything you want deployed into **`main`** through a pull request. This guide
deploys `main`. [Step 11.3](#113-deploy-a-different-branch) covers testing another branch.

---

## 2. Create the AWS resources

### 2.0 Budget alert (recommended, 2 minutes)
**Billing and Cost Management → Budgets → Create budget → Use a template → Monthly cost budget**
1. Set the amount to e.g. **$20**, and enter your email as a recipient.
2. Create it.

AWS then emails you when actual spend reaches 85%, or forecast spend reaches 100%. A forgotten
instance can't quietly run up a bill. It only notifies you: it doesn't stop anything.

### 2.1 S3 bucket for app data
S3 → **Create bucket**
- Name: e.g. `autosound-data-<team>` 📝 `<data-bucket>`
- Region: `us-west-2`
- Leave **Block all public access** on. This bucket stays private; the browser gets temporary
  signed links.

### 2.2 IAM role for the instance
IAM → Roles → **Create role**
1. Trusted entity: **AWS service → EC2**. Click Next, skip adding permissions, name it
   `autosound-ec2-role`, and create it.
2. Open the role → **Add permissions → Create inline policy → JSON**.
3. Paste [`../iam-instance-policy.json`](../iam-instance-policy.json), replacing `<bucket>` (twice)
   with `<data-bucket>`. Save it as `autosound-s3-ses`.

The containers get S3 and SES access through this role, so no AWS keys are stored on the server.

### 2.3 EC2 instance, security group and Elastic IP
EC2 → **Launch instance**

| Setting | Value |
|---|---|
| Name | `autosound` |
| AMI | **Amazon Linux 2023**, **64-bit (x86)**. The x86 build is required: the image's backup tools are x86-only. |
| Instance type | `t3.medium` (2 vCPU, 4 GB) |
| Key pair | Create one (RSA, `.pem`) and download it 📝 `<key>.pem`, then run `chmod 400 <key>.pem` |
| Network settings → security group | Create `autosound-sg` with inbound rules: **SSH 22 from My IP**, **HTTP 80 from Anywhere** (the UI), **Custom TCP 8000 from Anywhere** (edge clients such as CARLA) |
| Storage | **30 GiB** gp3 (the Docker images and build cache need room) |
| Advanced details → IAM instance profile | `autosound-ec2-role` |
| Advanced details → Metadata response hop limit | **2** (lets containers use the instance role; it's the Amazon Linux 2023 default, but check it) |

Then EC2 → **Elastic IPs → Allocate Elastic IP address → Actions → Associate**, and choose the
instance. 📝 `<elastic-ip>`. It keeps the same address across stop and start.

### 2.4 RDS PostgreSQL
RDS → **Create database** → **Standard create**

| Setting | Value |
|---|---|
| Engine | PostgreSQL **16.x** |
| Template | Free tier (or Dev/Test) |
| DB instance identifier | `autosound-db` |
| Master username / password | `autosound` / a strong password 📝 `<rds-password>` (letters and digits only) |
| Instance class | `db.t4g.micro` |
| Storage | 20 GiB |
| Connectivity | **Connect to an EC2 compute resource → `autosound`** |
| Public access | No |
| Additional configuration → Initial database name | **`autosound`** |

When the status is *Available*, copy the **Endpoint** 📝 `<rds-endpoint>`.

### 2.5 MongoDB Atlas
At cloud.mongodb.com:
1. **Create** a cluster: **M0 (Free)**, provider **AWS**, region **Oregon (us-west-2)**.
2. **Database Access → Add new database user:** username `app`, a strong password (letters and
   digits only), role "Read and write to any database".
3. **Network Access → Add IP address:** `<elastic-ip>/32`.
4. **Database → Connect → Drivers:** copy the string and insert `autosound` before the `?`:
   `mongodb+srv://app:<password>@<cluster>.mongodb.net/autosound?retryWrites=true&w=majority`
   📝 `<MONGO_URI>`

Atlas runs on AWS servers in `us-west-2`. AWS DocumentDB isn't real MongoDB and costs about
$55+/month, so this guide doesn't use it.

### 2.6 SES (alert emails)
SES (in `us-west-2`) → **Identities → Create identity → Email address**:
- Verify the **sender address** 📝 `<sender-email>`.
- Verify **the address that should receive alerts**: a real address you control, which you'll
  give the owner account in step 7.3 (sandbox mode only delivers to verified addresses).
- Click the link in each verification email.

---

## 3. Put the code on the instance

Only files tracked by git go to the instance: about 4 MB, including the trained model.
- **Never copy** `backend/.venv`, `frontend/node_modules`, `model/data`, the datasets or local
  `.env` files.
- The Docker build installs everything the containers need.
- You create the private settings file on the instance in step 6.

Connect first:
```bash
ssh -i <key>.pem ec2-user@<elastic-ip>
```

### Method 1: clone from GitHub (recommended)
The repository is public, so no GitHub credentials are needed:
```bash
sudo dnf install -y git
git clone https://github.com/anushatulasi-06/intelligent_cloud_platform_for_smart_cars.git ~/autosound
cd ~/autosound
git checkout main
git log -1 --oneline        # note which commit you're deploying
```

**If the repository is private,** give the instance a read-only deploy key:
```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/github_deploy && cat ~/.ssh/github_deploy.pub
```
1. On GitHub: repo → Settings → **Deploy keys → Add deploy key**. Paste the key and leave write
   access off.
2. On the instance:
   ```bash
   printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n' >> ~/.ssh/config
   git clone git@github.com:anushatulasi-06/intelligent_cloud_platform_for_smart_cars.git ~/autosound
   ```

### Method 2: copy from your laptop (unpushed code, or no GitHub access)
From the repository root **on your laptop**:
```bash
git ls-files -z | rsync -av --from0 --files-from=- -e "ssh -i <key>.pem" \
  ./ ec2-user@<elastic-ip>:~/autosound/
```
This copies only files git tracks, including uncommitted edits. Brand-new files need `git add`
first.
- The copy doesn't remove files you've deleted locally.
- Don't mix the two methods on one instance.

---

## 4. Install Docker

On the instance, in `~/autosound`:
```bash
bash infra/aws/docker/setup_ec2_docker.sh
```

The first run:
1. installs Docker plus the Compose and Buildx plugins, and starts Docker
2. copies [`../backend.env.production.example`](../backend.env.production.example) to
   `backend/.env`, then **stops**

## 5. Generate the two secrets

On the instance:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"                                          # JWT_SECRET
python3 -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"  # WIFI_ENCRYPTION_KEY
```

- `JWT_SECRET` signs login tokens (C1). Without your own value, anyone could forge a token.
- `WIFI_ENCRYPTION_KEY` encrypts stored Wi-Fi passwords (C2). **Keep a copy somewhere private.**
  If it's lost, the stored Wi-Fi passwords can't be read.

## 6. Fill in `backend/.env`

Run `nano backend/.env` and replace every `<...>`:

| Setting | Value |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://autosound:<rds-password>@<rds-endpoint>:5432/autosound` |
| `MONGO_URI` | `<MONGO_URI>` |
| `S3_BUCKET` | `<data-bucket>` |
| `AWS_REGION` | `us-west-2` |
| `JWT_SECRET`, `WIFI_ENCRYPTION_KEY` | from step 5 |
| `CORS_ORIGINS` | `http://<elastic-ip>` |
| `SES_FROM_EMAIL` | `<sender-email>` |

- Leave `S3_ENDPOINT_URL` and `S3_PUBLIC_ENDPOINT_URL` **empty**, and keep `MODEL_DIR` as it is.
- Keep `DEV_FAKE_EMAIL=false` and `DEV_FAKE_BACKUP=false` for real emails and backups.

Save it (Ctrl+O, Enter, Ctrl+X). The file is gitignored, so `git pull` never touches it, and it
isn't copied into the images.

## 7. Build and start

```bash
ENABLE_EDGE_DEMO=1 ENABLE_DEMO_DATA=1 bash infra/aws/docker/setup_ec2_docker.sh
```

The second run:
1. checks that nothing in `backend/.env` is still a placeholder
2. **builds the images.** The first build takes about 10 minutes: CPU torch, the backup tools,
   and the frontend build.
3. starts the containers. The `api` container runs the migrations (8 tables), seeds the demo data
   (4 users, 4 cars, 5 devices, the alert types, the active model and 25 alerts), and starts with
   one worker.
4. waits until both `:8000/health` and `/api/health` respond

Containers restart by themselves after a crash or a reboot.

| Option | Effect |
|---|---|
| `ENABLE_EDGE_DEMO=1` | Runs **`edge-demo`**, car 1's edge client. By default it's the **CARLA replay client**: each scenario in F11 plays a recorded CARLA drive, with its sound at the event, feeding C3 and C4 without a GPU or CARLA server. |
| `ENABLE_DEMO_DATA=1` | Runs **`demo-data`**: cars 2–3 keep moving on Map (F9) and Tracking (F10), and car 4 shows as offline. |
| `SKIP_SEED=1` | Skips the demo data. The demo passwords and device keys are public in the repository. |

**Pass the same options on every run** of the script. The demo feeds read `infra/aws/edge.env`,
which is created automatically and gitignored.

**Which edge client runs** is set by `EDGE_CLIENT` in `infra/aws/edge.env`:
- `replay_client` (default) replays the recordings in `backend/edge/carla_recordings/`. They ship
  with the code, so commit them before deploying.
- `demo_client` is a virtual car with an illustrated camera, driven by Start/Stop route.

After changing it: rerun the setup script with the same options, which recreates `edge-demo`. Details:
[edge client guide](../../../backend/edge/README.md#replay-recorded-carla-scenarios).

From your laptop, open `http://<elastic-ip>/`, and check that
`curl http://<elastic-ip>:8000/health` returns `{"status":"ok",...}`.

### 7.1 What the seed creates
The setup script ran `python -m app.db.seed`. It's idempotent: rerunning it adds only rows that are
missing and never overwrites anything.

| Store | Seeded data | Used by |
|---|---|---|
| PostgreSQL | 4 users: `owner@` (cars 1–3), `owner2@` (car 4), `iot@`, `cloud@` | C1 login, all role checks |
| PostgreSQL | 4 cars and 5 devices: a microphone per car, plus camera `cam-1` on car 1, all *registered* with known demo keys | C2 Device Manager, edge clients |
| PostgreSQL | 5 alert types with default severities, and model `cnn-v1` (active, threshold 0.75) | C4 classification, C5 Config |
| MongoDB | 25 alerts across the cars and categories | C4 Alerts, C1 dashboards |

**Not seeded, and where it comes from:**
- **Car positions:** the `demo-data` feed (`ENABLE_DEMO_DATA=1`) and the edge client.
- **Audio records (F13):** triggering scenarios in the Simulator.
- **Subscriptions:** turned on by the owner in F3 (step 9).
- **Backup jobs:** **Run backup now** in F14.
- **Audio for the 25 seeded alerts:** the next step.

### 7.2 Upload the audio for the seeded alerts
The 25 seeded alerts point to audio files in S3 that the seed doesn't create. Without this step,
their audio player in F7 stays empty. Alerts created later from Simulator scenarios have their
audio uploaded automatically. Skip this step if you deployed with `SKIP_SEED=1`.

The clips are in `backend/.local_audio/` **on your laptop**. That folder is gitignored, so it isn't
on the instance.
- Check that it's there: `ls backend/.local_audio/audio | head`
- If it's missing, create it with `python scripts/make_local_audio.py`. This needs the training
  dataset in `model/data/`.

1. **Laptop**, from the repository root: copy the clips to the instance.
   ```bash
   scp -r -i <key>.pem backend/.local_audio ec2-user@<elastic-ip>:~/autosound/backend/
   ```
2. **Instance**, in `~/autosound`: upload them to the data bucket. This uses the instance's IAM
   role and the bucket in `backend/.env`.
   ```bash
   C="sudo docker compose -f infra/aws/docker/docker-compose.yml"
   $C cp backend/.local_audio api:/tmp/local_audio
   $C exec -T api python - <<'EOF'
   from pathlib import Path
   from app.db import s3
   root = Path('/tmp/local_audio')
   files = sorted(root.rglob('*.wav'))
   for p in files:
       s3.upload_file(str(p), p.relative_to(root).as_posix(), 'audio/wav')
   print('uploaded', len(files), 'clips')
   EOF
   ```
   The upload runs inside the `api` container, which has the backend code and the IAM role
   access.
   It prints `uploaded N clips`, where N is at least 25 (one per seeded alert; extra clips do no harm).
3. **Check:** open any seeded alert in F7. Its audio plays.

Rerunning the upload is safe: it just replaces the same files.

### 7.3 Use a real email for the owner account
Alert emails (C4) go to the **car owner's account email**. The seeded owners use `@autosound.dev`
addresses, which you can't receive mail at or verify in SES, so no alert email could be delivered.
Point `owner@autosound.dev`, the owner of cars 1–3 including the edge demo's car 1, at an address
you control:

1. **SES:** verify that address (step 2.6), and click the link in the verification email.
2. **Instance**, in `~/autosound`, with your RDS password and endpoint:
   ```bash
   C="sudo docker compose -f infra/aws/docker/docker-compose.yml"
   $C exec api psql "postgresql://autosound:<rds-password>@<rds-endpoint>:5432/autosound" \
     -c "UPDATE users SET email = 'you@example.com' WHERE email = 'owner@autosound.dev';"
   ```
   It should print `UPDATE 1`.
3. **From now on, that owner signs in with your email.** The password stays `owner-dev-pass`.
4. *(Optional)* Do the same for `owner2@autosound.dev` (car 4) with a second verified address.

The `api` container runs the seed **every time it starts**, which re-creates an empty
`owner@autosound.dev` account with no cars. It's harmless, since the cars stay with your renamed
owner. To avoid it, add `SKIP_SEED=1` to every later run of `setup_ec2_docker.sh` (deploys,
rollbacks), alongside the `ENABLE_*` options.

## 8. Move existing local data (optional)

**Recommended: skip this.** The seed gives you clean demo data. If you do want to move data, do it
before step 7, or rerun step 7 with `SKIP_SEED=1` afterwards.

| Store | Steps |
|---|---|
| **PostgreSQL → RDS** | RDS is private, so restore from inside the `api` container. 1. On the laptop: `pg_dump -Fc -h localhost -U user autosound > autosound.dump`. 2. `scp -i <key>.pem autosound.dump ec2-user@<elastic-ip>:~/autosound/`. 3. On the instance: `sudo docker compose -f infra/aws/docker/docker-compose.yml cp autosound.dump api:/tmp/`, then `sudo docker compose -f infra/aws/docker/docker-compose.yml exec api pg_restore --no-owner -d "postgresql://autosound:<rds-password>@<rds-endpoint>:5432/autosound" /tmp/autosound.dump` |
| **MongoDB → Atlas** | Add your laptop's IP in Atlas Network Access. On the laptop: `brew install mongodb/brew/mongodb-database-tools`, then `mongodump --uri mongodb://localhost:27017/autosound --archive=autosound.archive --gzip`, then `mongorestore --uri "<MONGO_URI>" --archive=autosound.archive --gzip` |
| **MinIO → S3** | `aws s3 sync s3://autosound-audio-dev ./s3copy --endpoint-url http://127.0.0.1:9000` (with the MinIO keys), then `aws s3 sync ./s3copy s3://<data-bucket>` (with AWS keys that can write to it) |

Use the **same `WIFI_ENCRYPTION_KEY`** as your local `backend/.env`, or the moved Wi-Fi passwords
can't be decrypted.

---

## 9. Check every component

Open `http://<elastic-ip>/`.

| Account | Password |
|---|---|
| `owner@autosound.dev`, or **your email after step 7.3** | `owner-dev-pass` |
| `owner2@autosound.dev` | `owner2-dev-pass` |
| `iot@autosound.dev` | `iot-dev-pass` |
| `cloud@autosound.dev` | `cloud-dev-pass` |

**First:** as `owner`, turn on a few categories for car 1 under **Dashboard → Subscriptions (F3)**,
so alert emails are sent.

| Component | Account | Check |
|---|---|---|
| **C1** | each role | Login sends each role to its home page. Dashboard F2 (owner) and F6 (cloud) show data. F3 toggles. Map F9 and Tracking F10 show green moving cars and a grey car 4. |
| **C2** | `iot@` | F4: add a device with Wi-Fi (shows the key once) and edit it. F5: configuration and command history. **F11** (`/simulator?car_id=1`): click a scenario, and the recorded CARLA drive plays with its sound at the event. Start/Stop route pauses and resumes it. |
| **C3** | `iot@` / `cloud@` | Trigger a sound scenario in F11. **F13** shows the record as processed, and its audio plays. As cloud, **Retry** works on a failed record. |
| **C4** | `cloud@`, then `owner@` | The new alert appears in the drawer and F12. F7 shows class probabilities and plays the audio, for new alerts and seeded ones (step 7.2). Ack/Resolve work. **An email arrives** at your step 7.3 address. `owner2` sees only car 4. |
| **C5** | `cloud@` | F8a and F8b edits save. **F14** shows PostgreSQL, MongoDB and S3 **Healthy**. **Run backup now** succeeds, and the files appear in `<data-bucket>` under `backups/`. |

**Automated end-to-end check (C2 → C3 → C4),** from the repository root on your laptop:
```bash
backend/.venv/bin/python scripts/verify_components.py --base-url http://<elastic-ip>:8000
```
It leaves one test record and one test alert behind.

## 10. Connect CARLA (optional)

CARLA needs a machine with full NVIDIA graphics drivers. Colab can't render its camera.
1. Stop the edge demo on the instance, since both use the same devices:
   `sudo docker compose -f infra/aws/docker/docker-compose.yml stop edge-demo`
2. On the CARLA machine, follow [`infra/README.md`](../../README.md#edge-clients-and-carla), with:
   ```bash
   export EDGE_API_URL=http://<elastic-ip>:8000      # http://<elastic-ip>/api also works
   export EDGE_CAR_ID=1 EDGE_DEVICE_ID=1 EDGE_DEVICE_KEY=dev-device-key-mic-1 \
          EDGE_CAMERA_DEVICE_ID=5 EDGE_CAMERA_DEVICE_KEY=dev-device-key-cam-1
   ```
3. When done: `sudo docker compose -f infra/aws/docker/docker-compose.yml start edge-demo`

---

## 11. Day-to-day operations

All commands run on the instance in `~/autosound`. Start with this shortcut:
```bash
C="sudo docker compose -f infra/aws/docker/docker-compose.yml"
```

### 11.1 Status and logs
```bash
$C ps                     # api should show "healthy"
$C logs -f api            # also: frontend, edge-demo, demo-data
$C restart api
```

### 11.2 Deploy new code
1. Merge to `main`.
2. **Code from GitHub (Method 1):**
   ```bash
   git pull
   ENABLE_EDGE_DEMO=1 ENABLE_DEMO_DATA=1 bash infra/aws/docker/setup_ec2_docker.sh
   ```
3. **Code copied with Method 2:** copy again from your laptop, then rerun the setup script with the
   same options.

The script rebuilds the images (only the changed layers, so it's fast after the first time), runs
migrations (which only add, never delete) and restarts the containers. Backend and frontend
update together.

### 11.3 Deploy a different branch
To test a branch before merging it:
```bash
git fetch origin && git checkout feature/deployment
ENABLE_EDGE_DEMO=1 ENABLE_DEMO_DATA=1 bash infra/aws/docker/setup_ec2_docker.sh
```
Switch back with `git checkout main && git pull`, then run the script again.

### 11.4 Roll back
```bash
git log --oneline -10                      # find the last good commit
git checkout <commit>
ENABLE_EDGE_DEMO=1 ENABLE_DEMO_DATA=1 bash infra/aws/docker/setup_ec2_docker.sh
```
Migrations aren't undone, but they only add tables or columns, so older code still runs. When the
fix is on `main`, run `git checkout main && git pull`, then the script again.

For a bad **model**, there's no redeploy: reactivate the previous version in F8b.

### 11.5 Pause, resume and cost
- **Pause:** EC2 → Instance state → **Stop**, and RDS → Actions → **Stop temporarily** (RDS
  restarts by itself after 7 days).
- **Resume:** start both. Docker and the containers start at boot, and the Elastic IP keeps the
  address.
- **Free disk space** after many deploys: `sudo docker system prune -f`
- **Approximate monthly cost:**
  - EC2 `t3.medium`: about $30
  - RDS `db.t4g.micro`: about $12–15 (free for 12 months on a new account)
  - Atlas M0: free
  - S3 and SES: cents
  - The Elastic IP is billed while the instance is stopped.

### 11.6 Tear everything down
1. **Terminate** the EC2 instance.
2. **Release** the Elastic IP.
3. **Delete** the RDS database. Optionally keep a final snapshot.
4. **Empty, then delete** the S3 bucket.
5. **Delete** the Atlas cluster.
6. Delete the IAM role `autosound-ec2-role` and the SES identities.

---

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Setup stops with "still has <...> placeholders" | It lists the settings left to fill in `backend/.env`. |
| Script says to use an x86_64 instance | You launched an ARM (Graviton) instance. Relaunch with **64-bit (x86)**. |
| Build fails while installing `postgresql-client-16` or `mongodb-database-tools` | Network or repository issue. Rerun the script. If it keeps failing, share the build log. |
| `api` keeps restarting | `$C logs api`. Usually a wrong `DATABASE_URL`/`MONGO_URI`, RDS not connected to the instance (step 2.4), or Atlas Network Access missing the Elastic IP. |
| F14 shows S3 unhealthy, "Unable to locate credentials" | Set the metadata hop limit to 2 (EC2 → Actions → Instance settings → Modify instance metadata options), and check the IAM role is attached. |
| Page doesn't load at `http://<elastic-ip>/` | Security group port 80 is missing, or `$C ps` shows `frontend` isn't running (it waits for `api` to be healthy). |
| No alert emails | The owner's email wasn't changed to your verified address (step 7.3), `DEV_FAKE_EMAIL` is still `true`, SES identities aren't verified, or the owner's subscription is off in F3. |
| Simulator says "Waiting for an edge client" | `edge-demo` isn't running: run the script with `ENABLE_EDGE_DEMO=1`, and check `$C logs edge-demo`. `EDGE_CLIENT` in `edge.env` must be `replay_client` or `demo_client`, and the replay client needs the recordings committed in `backend/edge/carla_recordings/` (they're built into the image). |
| "No space left on device" | `sudo docker system prune -f`, or increase the EBS volume size. |
