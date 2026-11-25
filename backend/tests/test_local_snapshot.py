"""Disposable QA must not copy private settings or old evidence into its source snapshot."""
import importlib.util
from pathlib import Path


def test_snapshot_excludes_private_edge_settings_credentials_and_evidence(tmp_path, monkeypatch):
    source = tmp_path / "repo"
    target = tmp_path / "snapshot"
    target.mkdir()
    entries = {
        "backend/app/main.py": "public source",
        "backend/.env.example": "public template",
        "backend/.env": "synthetic private canary",
        "infra/aws/edge.env": "synthetic private canary",
        "backend/.local_bootstrap/credentials.json": "synthetic private canary",
        "backend/.local_evidence/result.json": "synthetic private canary",
        "backend/.local_edge/buffer.key": "synthetic private canary",
    }
    for name, content in entries.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    spec = importlib.util.spec_from_file_location(
        "snapshot_verifier", Path(__file__).resolve().parents[2] / "scripts/verify_local.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    monkeypatch.setattr(verifier, "ROOT", source)
    monkeypatch.setattr(verifier.subprocess, "check_output", lambda *args, **kwargs:
                        ("\0".join(entries) + "\0").encode())
    assert verifier.snapshot_source(target) == 2
    copied = {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}
    assert copied == {"backend/app/main.py", "backend/.env.example"}
    assert all("private canary" not in path.read_text() for path in target.rglob("*") if path.is_file())
