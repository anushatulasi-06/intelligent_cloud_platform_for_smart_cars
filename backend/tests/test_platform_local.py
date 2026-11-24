"""Local verifier boundaries: refuse external targets, unsafe namespaces and real side effects."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "verify_platform_local", Path(__file__).resolve().parents[2] / "scripts/verify_platform_local.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


@pytest.mark.parametrize("url", ["https://s3.amazonaws.com", "http://localhost:18000", "http://169.254.169.254",
    "http://127.0.0.1.attacker.example", "http://secret:password@127.0.0.1:18000", "http://127.0.0.1:bad"])
def test_external_or_ambiguous_targets_are_rejected_before_client_creation(monkeypatch, url):
    monkeypatch.setattr(verifier.httpx, "Client", lambda **kw: pytest.fail("HTTP client must not be created"))
    with pytest.raises(verifier.VerificationError):
        verifier.LocalPlatformVerification(url, "http://127.0.0.1:19000", "bucket", {})


@pytest.mark.parametrize("baseline,interfaces", [
    ("net:[2]", "lo"), ("net:[1]", "lo\neth0"), ("invalid", "lo")])
def test_network_guard_rejects_same_namespace_or_nonloopback_interface(monkeypatch, baseline, interfaces):
    monkeypatch.setattr(verifier.sys, "platform", "linux")
    rows = 'header\nheader\n' + '\n'.join(name + ': 0' for name in interfaces.splitlines())
    monkeypatch.setattr(verifier.Path, "read_text", lambda self: rows)
    monkeypatch.setattr(verifier.os, "readlink", lambda path: "net:[2]")
    with pytest.raises(verifier.VerificationError):
        verifier.isolated_namespace(baseline)


@pytest.mark.parametrize("email,backup", [("ses", "simulated"), ("logged", "real")])
def test_live_verifier_refuses_real_side_effect_modes_before_mutation(email, backup):
    check = verifier.LocalPlatformVerification.__new__(verifier.LocalPlatformVerification)
    mutations = []
    check.evidence = {"checks": []}
    check.login = lambda: None
    check.request = lambda *a, **kw: SimpleNamespace()
    check.get = lambda *a, **kw: {"status": "ready", "scope": {"side_effect_modes": {
        "email": email, "backup": backup}}}
    check.dashboards_and_scope = lambda: mutations.append("after safety gate")
    with pytest.raises(verifier.VerificationError, match="Refusing mutation"):
        check.run()
    assert mutations == []


def test_cleanup_attempts_every_restore_after_one_failure():
    calls = []
    check = verifier.LocalPlatformVerification.__new__(verifier.LocalPlatformVerification)
    check.evidence = {"cleanup": []}
    def failed():
        calls.append("failed")
        raise RuntimeError
    check.restore = [("first", lambda: calls.append("first")), ("failed", failed),
                     ("last", lambda: calls.append("last"))]
    check.client = SimpleNamespace(close=lambda: calls.append("close"))
    with pytest.raises(verifier.VerificationError, match="restore"):
        check.cleanup()
    assert calls == ["last", "failed", "first", "close"]
