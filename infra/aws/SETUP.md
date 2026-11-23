# Setup: Option A (scripts, frontend on S3)

This guide covers the complete AWS setup for all five components (C1–C5), from an empty account
to a working, checked deployment, plus updates, rollback and teardown.

| Part | Where it runs |
|---|---|
| API (C1–C5), C3 processing worker, C2 device sweeper, C4 model | **EC2**, as a systemd service, at `http://<elastic-ip>:8000` |
| UI (React build) | **S3 static website** |
| Data | **RDS PostgreSQL** (users, cars, devices, commands, config, models, jobs) · **MongoDB Atlas** (alerts, audio records, positions) · **S3** (audio clips, camera frames, backups, archives) |
| Email | **SES** |

There is no load balancer. For the Docker alternative, see [`docker/SETUP.md`](docker/SETUP.md).

**Time:** about 2–3 hours the first time. **Cost:** roughly $30–45/month while running (see
[step 12.5](#125-pause-resume-and-cost)).

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
| `<frontend-bucket>` | 2.7 | `autosound-web-group8` |

---

## 1. Before you start

**Accounts:**
- An AWS account with console access. Use one region for everything; this guide uses `us-west-2`.
- A MongoDB Atlas account (free) at cloud.mongodb.com.

**On your laptop:**
- `git` and an SSH client (included with macOS).
- Node **20.19+** (`node -v`), to build the frontend.
- The AWS CLI: `brew install awscli`. You'll configure it in step 2.8.
- A clone of the repository.

**Code:** merge everything you want deployed into **`main`** through a pull request. This guide
deploys `main`. [Step 12.3](#123-deploy-a-different-branch) covers testing another branch.

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
3. Paste [`iam-instance-policy.json`](iam-instance-policy.json), replacing `<bucket>` (twice) with
   `<data-bucket>`. Save it as `autosound-s3-ses`.

The instance gets S3 and SES access through this role, so no AWS keys are stored on the server.

### 2.3 EC2 instance, security group and Elastic IP
EC2 → **Launch instance**

| Setting | Value |
|---|---|
| Name | `autosound` |
| AMI | **Amazon Linux 2023**, **64-bit (x86)** |
| Instance type | `t3.medium` (2 vCPU, 4 GB; the model and `pip install torch` need the memory) |
| Key pair | Create one (RSA, `.pem`) and download it 📝 `<key>.pem`, then run `chmod 400 <key>.pem` |
| Network settings → security group | Create `autosound-sg` with inbound rules: **SSH 22 from My IP**, **Custom TCP 8000 from Anywhere (0.0.0.0/0)** (browsers and edge clients call the API) |
| Storage | 20 GiB gp3 |
| Advanced details → IAM instance profile | `autosound-ec2-role` |

Then EC2 → **Elastic IPs → Allocate Elastic IP address → Actions → Associate**, and choose the
instance. 📝 `<elastic-ip>`

The Elastic IP keeps the same address when the instance is stopped and started. The frontend is
built with this address, so it must not change.

### 2.4 RDS PostgreSQL
RDS → **Create database** → **Standard create**

| Setting | Value |
|---|---|
| Engine | PostgreSQL **16.x** |
| Template | Free tier (or Dev/Test) |
| DB instance identifier | `autosound-db` |
| Master username / password | `autosound` / a strong password 📝 `<rds-password>` (letters and digits only, so it needs no URL-encoding) |
| Instance class | `db.t4g.micro` |
| Storage | 20 GiB |
| Connectivity | **Connect to an EC2 compute resource → `autosound`** (RDS opens port 5432 to the instance only) |
| Public access | No |
| Additional configuration → Initial database name | **`autosound`** |

It takes 5–10 minutes. When the status is *Available*, copy the **Endpoint** 📝 `<rds-endpoint>`.

### 2.5 MongoDB Atlas
At cloud.mongodb.com:
1. **Create** a cluster: **M0 (Free)**, provider **AWS**, region **Oregon (us-west-2)**.
2. **Database Access → Add new database user:** password authentication, username `app`, a strong
   password (letters and digits only), role "Read and write to any database".
3. **Network Access → Add IP address:** `<elastic-ip>/32`.
4. **Database → Connect → Drivers:** copy the string and insert `autosound` before the `?`:
   `mongodb+srv://app:<password>@<cluster>.mongodb.net/autosound?retryWrites=true&w=majority`
   📝 `<MONGO_URI>`

Atlas runs on AWS servers in `us-west-2`, next to your instance. AWS's own DocumentDB isn't real
MongoDB and costs about $55+/month, so this guide doesn't use it.

### 2.6 SES (alert emails)
SES (in `us-west-2`) → **Identities → Create identity → Email address**:
- Verify the **sender address** 📝 `<sender-email>`.
- Verify **the address that should receive alerts**: a real address you control, which you'll
  give the owner account in step 7.3. New accounts are in sandbox mode, which only delivers to
  verified addresses.
- Click the link in each verification email.

### 2.7 S3 bucket for the frontend
S3 → **Create bucket**
- Name: e.g. `autosound-web-<team>` 📝 `<frontend-bucket>`
- Region: `us-west-2`
- **Uncheck "Block all public access"** and confirm. The UI must be publicly readable; the deploy
  script adds the read-only policy and turns on website hosting.

The website URL will be `http://<frontend-bucket>.s3-website-us-west-2.amazonaws.com`.

### 2.8 AWS CLI access from your laptop (for the frontend upload)
1. IAM → Users → **Create user**, e.g. `autosound-deployer`. No console access is needed.
2. Attach an inline policy that allows these actions on the frontend bucket:
   - `s3:ListBucket`, `s3:PutBucketWebsite` and `s3:PutBucketPolicy` on
     `arn:aws:s3:::<frontend-bucket>`
   - `s3:PutObject`, `s3:DeleteObject` and `s3:GetObject` on `arn:aws:s3:::<frontend-bucket>/*`
3. Open the user → **Security credentials → Create access key → Command Line Interface**.
4. On your laptop, run `aws configure` and enter the key, the secret, region `us-west-2`, and
   output `json`.

---

## 3. Put the code on the instance

Only files tracked by git go to the instance: about 4 MB, including the trained model
(`model/artifacts/cnn-v1/`).
- **Never copy** `backend/.venv`, `frontend/node_modules`, `model/data`, the datasets or your
  local `.env` files. Your local folder is about 2.4 GB because of these.
- The setup script builds what the instance needs.
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
- **Updates** with this method are covered in step 12.2.
- The copy doesn't remove files you've deleted locally.
- Don't mix the two methods on one instance.

---

## 4. Install the system and Python packages

On the instance, in `~/autosound`:
```bash
bash infra/aws/setup_ec2.sh
```

The first run:
1. installs Python 3.12, the PostgreSQL 16 client (`pg_dump`), the MongoDB tools (`mongodump`)
   and a font for camera frames
2. creates `backend/.venv` with CPU torch 2.5.1 and the backend requirements
3. copies [`backend.env.production.example`](backend.env.production.example) to `backend/.env`,
   then **stops**

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
| `CORS_ORIGINS` | `http://<frontend-bucket>.s3-website-us-west-2.amazonaws.com` |
| `SES_FROM_EMAIL` | `<sender-email>` |

- Leave `S3_ENDPOINT_URL` and `S3_PUBLIC_ENDPOINT_URL` **empty**, so the backend uses real AWS S3.
- Keep `DEV_FAKE_EMAIL=false` and `DEV_FAKE_BACKUP=false` for real emails and backups.

Save it (Ctrl+O, Enter, Ctrl+X). The file is gitignored, so `git pull` never touches it.

## 7. Start the backend

```bash
ENABLE_EDGE_DEMO=1 ENABLE_DEMO_DATA=1 bash infra/aws/setup_ec2.sh
```

The second run:
1. checks that nothing in `backend/.env` is still a placeholder (if something is, it lists it and
   stops)
2. runs the database migrations: 8 tables, including C2's command queue
3. seeds the demo data: 4 users, 4 cars, 5 devices, the alert types, the active model and 25 alerts
4. installs and starts the **`autosound-api`** service, which starts at boot and restarts if it
   crashes
5. waits for `/health`

| Option | Effect |
|---|---|
| `ENABLE_EDGE_DEMO=1` | Runs **`autosound-edge`**: a virtual car 1 with an illustrated camera. The Simulator (F11) is live, and its sound scenarios feed C3 and C4, without CARLA. |
| `ENABLE_DEMO_DATA=1` | Runs **`autosound-demo-data`**: cars 2–3 keep moving on Map (F9) and Tracking (F10), and car 4 shows as offline. |
| `SKIP_SEED=1` | Skips the demo data. The demo passwords and device keys are public in the repository. |

The demo feeds read `infra/aws/edge.env`, which is created automatically and gitignored.

From your laptop, `curl http://<elastic-ip>:8000/health` should return `{"status":"ok",...}`.
The API docs are at `http://<elastic-ip>:8000/docs`.

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
- **Subscriptions:** turned on by the owner in F3 (step 10).
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
   cd backend
   .venv/bin/python - <<'EOF'
   from pathlib import Path
   from app.db import s3
   root = Path('.local_audio')
   files = sorted(root.rglob('*.wav'))
   for p in files:
       s3.upload_file(str(p), p.relative_to(root).as_posix(), 'audio/wav')
   print('uploaded', len(files), 'clips')
   EOF
   cd ..
   ```
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
   psql "postgresql://autosound:<rds-password>@<rds-endpoint>:5432/autosound" \
     -c "UPDATE users SET email = 'you@example.com' WHERE email = 'owner@autosound.dev';"
   ```
   It should print `UPDATE 1`.
3. **From now on, that owner signs in with your email.** The password stays `owner-dev-pass`.
4. *(Optional)* Do the same for `owner2@autosound.dev` (car 4) with a second verified address.

Running the seed again (`setup_ec2.sh` without `SKIP_SEED=1`) re-creates an empty
`owner@autosound.dev` account with no cars. It's harmless, since the cars stay with your renamed
owner, but pass `SKIP_SEED=1` on later `setup_ec2.sh` runs to avoid it. `deploy_backend.sh` never
seeds.

## 8. Deploy the frontend (from your laptop)

From the repository root, on the same commit as the instance:
```bash
git checkout main && git pull
FRONTEND_BUCKET=<frontend-bucket> API_BASE_URL=http://<elastic-ip>:8000 \
  bash infra/aws/deploy_frontend.sh
```

The script:
1. checks your Node version
2. builds the UI against the real API
3. turns on website hosting, so page reloads on any route work
4. applies the public-read policy
5. uploads the build and prints the website URL

If that URL differs from `CORS_ORIGINS` in step 6, fix it on the instance and run
`sudo systemctl restart autosound-api`.

## 9. Move existing local data (optional)

**Recommended: skip this.** The seed gives you clean demo data, and your local databases only
hold test data. If you do want to move them, do it before step 7, or rerun step 7 with
`SKIP_SEED=1` afterwards.

| Store | Steps |
|---|---|
| **PostgreSQL → RDS** | RDS is private, so go through the instance. 1. On the laptop: `pg_dump -Fc -h localhost -U user autosound > autosound.dump`. 2. `scp -i <key>.pem autosound.dump ec2-user@<elastic-ip>:~`. 3. On the instance: `pg_restore --no-owner -d "postgresql://autosound:<rds-password>@<rds-endpoint>:5432/autosound" ~/autosound.dump` |
| **MongoDB → Atlas** | Add your laptop's IP in Atlas Network Access. On the laptop: `brew install mongodb/brew/mongodb-database-tools`, then `mongodump --uri mongodb://localhost:27017/autosound --archive=autosound.archive --gzip`, then `mongorestore --uri "<MONGO_URI>" --archive=autosound.archive --gzip` |
| **MinIO → S3** | `aws s3 sync s3://autosound-audio-dev ./s3copy --endpoint-url http://127.0.0.1:9000` (with the MinIO keys), then `aws s3 sync ./s3copy s3://<data-bucket>` (with AWS keys that can write to it) |

Use the **same `WIFI_ENCRYPTION_KEY`** as your local `backend/.env`, or the moved Wi-Fi passwords
can't be decrypted.

---

## 10. Check every component

Open `http://<frontend-bucket>.s3-website-us-west-2.amazonaws.com`.

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
| **C2** | `iot@` | F4: add a device with Wi-Fi (shows the key once) and edit it. F5: configuration and command history. **F11** (`/simulator?car_id=1`): live frame and position, **Start/Stop route**. |
| **C3** | `iot@` / `cloud@` | Trigger a sound scenario in F11. **F13** (Device Manager → Device data) shows the record as processed, and its audio plays. As cloud, **Retry** works on a failed record. |
| **C4** | `cloud@`, then `owner@` | The new alert appears in the drawer and F12. F7 shows class probabilities and plays the audio, for new alerts and seeded ones (step 7.2). Ack/Resolve work. **An email arrives** at your step 7.3 address. `owner2` sees only car 4. |
| **C5** | `cloud@` | F8a severity edits and F8b threshold (0.50–0.95) save. **F14** shows PostgreSQL, MongoDB and S3 **Healthy**. **Run backup now** succeeds, and the files appear in `<data-bucket>` under `backups/`. |

**Automated end-to-end check (C2 → C3 → C4),** from the repository root on your laptop:
```bash
backend/.venv/bin/python scripts/verify_components.py --base-url http://<elastic-ip>:8000
```
It leaves one test record and one test alert behind.

## 11. Connect CARLA (optional)

CARLA needs a machine with full NVIDIA graphics drivers. Colab can't render its camera.
1. Stop the edge demo on the instance, since both use the same devices:
   `sudo systemctl stop autosound-edge`
2. On the CARLA machine, follow [`infra/README.md`](../README.md#edge-clients-and-carla), with:
   ```bash
   export EDGE_API_URL=http://<elastic-ip>:8000
   export EDGE_CAR_ID=1 EDGE_DEVICE_ID=1 EDGE_DEVICE_KEY=dev-device-key-mic-1 \
          EDGE_CAMERA_DEVICE_ID=5 EDGE_CAMERA_DEVICE_KEY=dev-device-key-cam-1
   ```
3. When done: `sudo systemctl start autosound-edge`

---

## 12. Day-to-day operations

All commands run on the instance in `~/autosound`, unless they say laptop.

### 12.1 Status and logs
```bash
sudo systemctl status autosound-api            # also: autosound-edge, autosound-demo-data
journalctl -u autosound-api -f                 # live logs; Ctrl+C stops watching, not the service
sudo systemctl restart autosound-api
```

### 12.2 Deploy new code
1. Merge to `main`.
2. **Backend** (code from GitHub, Method 1):
   ```bash
   bash infra/aws/deploy_backend.sh
   ```
   It pulls, installs dependencies, runs migrations (which only add, never delete), restarts the
   API and demo feeds, and checks health.
3. **Backend** (code copied with Method 2): copy again from your laptop, then
   `SKIP_PULL=1 bash infra/aws/deploy_backend.sh`.
4. **Frontend** (laptop, only if the UI changed): rerun the step 8 command.

### 12.3 Deploy a different branch
To test a branch before merging it:
```bash
BRANCH=feature/deployment bash infra/aws/deploy_backend.sh
```
Also rebuild the frontend from that branch on your laptop if the UI differs. Switch back with
`BRANCH=main bash infra/aws/deploy_backend.sh`.

### 12.4 Roll back
```bash
git log --oneline -10                      # find the last good commit
git checkout <commit>
SKIP_PULL=1 bash infra/aws/deploy_backend.sh
```
Redeploy the frontend from the same commit if the UI changed. Migrations aren't undone, but they
only add tables or columns, so older code still runs. When the fix is on `main`, run
`git checkout main && bash infra/aws/deploy_backend.sh`.

For a bad **model**, there's no redeploy: reactivate the previous version in F8b.

### 12.5 Pause, resume and cost
- **Pause:** EC2 → Instance state → **Stop**, and RDS → Actions → **Stop temporarily** (RDS
  restarts by itself after 7 days).
- **Resume:** start both. The services start at boot, and the Elastic IP keeps the address.
- **Approximate monthly cost:**
  - EC2 `t3.medium`: about $30
  - RDS `db.t4g.micro`: about $12–15 (free for 12 months on a new account)
  - Atlas M0: free
  - S3 and SES: cents
  - The Elastic IP is billed while the instance is stopped.

### 12.6 Tear everything down
1. **Terminate** the EC2 instance.
2. **Release** the Elastic IP.
3. **Delete** the RDS database. Optionally keep a final snapshot.
4. **Empty, then delete** both S3 buckets.
5. **Delete** the Atlas cluster.
6. Delete the IAM role `autosound-ec2-role`, the user `autosound-deployer` and its access key,
   and the SES identities.

---

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Setup stops with "still has <...> placeholders" | It lists the settings left to fill in `backend/.env`. |
| `/health` never responds | `journalctl -u autosound-api -n 100`. Usually a wrong `DATABASE_URL`/`MONGO_URI`, RDS not connected to the instance (step 2.4), or Atlas Network Access missing the Elastic IP. |
| Can't reach `:8000` from the laptop | Security group port 8000 is missing, or you're using the old public IP instead of the Elastic IP. |
| UI loads, but every call fails | `CORS_ORIGINS` doesn't exactly match the website URL (no trailing slash), or the frontend was built with the wrong `API_BASE_URL`; redeploy it. |
| F14 shows S3 unhealthy, or uploads fail | The IAM role isn't attached (EC2 → Actions → Security → Modify IAM role), or the policy still says `<bucket>`. |
| No alert emails | The owner's email wasn't changed to your verified address (step 7.3), `DEV_FAKE_EMAIL` is still `true`, the sender or recipient isn't verified in SES, or the owner's subscription is off in F3. |
| Backup fails | Check `journalctl -u autosound-api`. It needs `pg_dump` 16 and `mongodump`; rerun `setup_ec2.sh`. |
| Simulator says "Waiting for an edge client" | `sudo systemctl status autosound-edge`. It must be enabled (`ENABLE_EDGE_DEMO=1`), and stopped only while CARLA is connected. |
| `deploy_frontend.sh` fails with AccessDenied | The laptop's IAM user is missing a permission from step 2.8, or Block Public Access is still on for the frontend bucket. |
