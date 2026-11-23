# AWS deployment

There are two ways to deploy all five components (C1–C5) to one EC2 instance, with no load
balancer. Both use RDS PostgreSQL, MongoDB Atlas, S3 and SES. Pick one and follow its guide from
start to finish.

| | Option A: scripts | Option B: Docker |
|---|---|---|
| **Guide** | **[`SETUP.md`](SETUP.md)** | **[`docker/SETUP.md`](docker/SETUP.md)** |
| UI | S3 static website | `http://<elastic-ip>/` (nginx on EC2) |
| API | `http://<elastic-ip>:8000` | `http://<elastic-ip>:8000` |
