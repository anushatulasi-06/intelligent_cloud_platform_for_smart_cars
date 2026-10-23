# intelligent_cloud_platform_for_smart_cars

CMPE 281 (Group 8) — cloud platform that classifies audio from CARLA-simulated cars
and raises role-based alerts.

## Deployed audio classifier: `model/`

The backend imports only `model.orchestrator`:

```python
from model.orchestrator import preprocess_and_classify

category, confidence, is_anomaly, probs = preprocess_and_classify("clip.wav")
```

- Categories: emergency, in_vehicle, human, traffic, environmental
- Anomaly flag: confidence < `CONFIDENCE_THRESHOLD` (0.75) in `model/config.py`
- Held-out test set: accuracy 0.84, macro-F1 0.81 (`model/checkpoints/evaluation_report.json`)
- Trained only on the professor-provided `Audio_Datasets/` (not in git)

Setup and tests, from the repo root:

```bash
pip install -r model/requirements.txt
python -m pytest model/tests -v
```

## Repository layout

| Folder | Contents |
| --- | --- |
| `model/` | Deployed classifier: data prep, training, evaluation, inference, tests |
| `backend/` | FastAPI service: auth/RBAC, device manager, data ingestion, alert tracking, DB access |
| `frontend/` | React role-based dashboard (owner, IoT staff, cloud staff) |
| `edge_client/` | Simulated car client that uploads audio events to the backend |
| `infra/` | AWS deployment (EC2 Auto Scaling, ALB, RDS, S3, CloudWatch) |
| `scripts/` | Audio utilities (`normalize_audio.py`) |
| `Audio_Datasets/` | Professor-provided training audio (local only, not in git) |
