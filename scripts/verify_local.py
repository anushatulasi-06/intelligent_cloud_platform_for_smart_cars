#!/usr/bin/env python3
"""Reproducible Linux QA with disposable stores and kernel-enforced network isolation.

Requires preinstalled Python dependencies, PostgreSQL 16, MongoDB 7, MinIO, Node,
frontend node_modules/ and a Playwright Chromium browser. Installs/downloads nothing.

Example (replace paths with your existing local installations)::

    python3 scripts/verify_local.py --python /path/to/venv/bin/python \
      --postgres-bin /path/to/postgresql/bin --mongod /path/to/mongod \
      --minio /path/to/minio --node-modules /path/to/frontend/node_modules \
      --chromium /path/to/chrome --output /path/to/new-evidence-directory

The output directory must be empty. Source is copied from git's tracked and
unignored files into a private temporary directory; real .env files, databases,
datasets and caches are excluded. The source repository and existing stores are
never configured, started, stopped or reset. Optional training-dataset tests may
skip. SES and backups are explicitly simulated; CARLA and AWS are not tested.

unshare(1), iproute2, unprivileged user/network namespaces and a non-root caller
are required. There is deliberately no fallback to running without isolation.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from verify_history_local import credentials_env, stage_audio


PG_PORT, MONGO_PORT, S3_PORT, CONSOLE_PORT, API_PORT, UI_PORT = 15432, 17017, 19000, 19001, 18000, 15173
DATABASE = "autosound_local_qa"
API = f"http://127.0.0.1:{API_PORT}"
UI = f"http://127.0.0.1:{UI_PORT}"
S3 = f"http://127.0.0.1:{S3_PORT}"
ROOT = Path(__file__).resolve().parents[1]


class CheckFailed(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def network_evidence(ip, host_netns):
    """Fail closed before any service, SDK, browser or test is started."""
    namespace = os.readlink("/proc/self/ns/net")
    check(namespace != host_netns, "Refusing to run in the caller's network namespace")
    links = json.loads(subprocess.check_output([ip, "-j", "link", "show"], text=True))
    routes = json.loads(subprocess.check_output([ip, "-j", "route", "show", "table", "all"], text=True))
    routes6 = json.loads(subprocess.check_output([ip, "-j", "-6", "route", "show", "table", "all"], text=True))
    check([link["ifname"] for link in links] == ["lo"], "Network namespace must contain only loopback")
    check("UP" in links[0].get("flags", []), "Loopback is not up")
    check(all(row.get("dev") == "lo" and row.get("dst") != "default" for row in routes + routes6),
          "Network namespace has a non-loopback route")
    unreachable = subprocess.run([ip, "route", "get", "192.0.2.1"], capture_output=True, text=True)
    check(unreachable.returncode != 0, "External address unexpectedly has a route")
    return {"namespace": namespace, "different_from_caller": True, "interfaces": ["lo"],
            "ipv4_routes": routes, "ipv6_routes": routes6, "external_route_unreachable": True}


def sanitized_env(runtime, args):
    """Build from an allowlist; never inherit cloud credentials, profiles, proxies or app URLs."""
    for name in ("home", "cache", "config", "tmp"):
        (runtime / name).mkdir(mode=0o700)
    env = {
        "PATH": os.pathsep.join(dict.fromkeys([str(args.python.parent), str(args.node.parent),
                                              str(args.postgres_bin), "/usr/local/bin", "/usr/bin", "/bin"])),
        "HOME": str(runtime / "home"), "TMPDIR": str(runtime / "tmp"),
        "XDG_CACHE_HOME": str(runtime / "cache"), "XDG_CONFIG_HOME": str(runtime / "config"),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC", "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "AWS_ACCESS_KEY_ID": "local-qa-access", "AWS_SECRET_ACCESS_KEY": "local-qa-secret-only",
        "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "AWS_EC2_METADATA_DISABLED": "true", "AWS_REGION": "us-west-2", "AWS_DEFAULT_REGION": "us-west-2",
        "MINIO_ROOT_USER": "local-qa-access", "MINIO_ROOT_PASSWORD": "local-qa-secret-only",
        "MINIO_BROWSER": "off", "MINIO_UPDATE": "off",
        "APP_ENV": "development", "DEV_FAKE_EMAIL": "true", "DEV_FAKE_BACKUP": "true",
        "SES_FROM_EMAIL": "local-qa@example.invalid", "LOCAL_AUDIO_DIR": "",
        "LOCAL_BACKUP_DIR": str(runtime / "fake-backups"),
        "DATABASE_URL": f"postgresql+psycopg2://qa@127.0.0.1:{PG_PORT}/{DATABASE}",
        "MONGO_URI": f"mongodb://127.0.0.1:{MONGO_PORT}/{DATABASE}",
        "S3_BUCKET": "autosound-local-qa", "S3_ENDPOINT_URL": S3, "S3_PUBLIC_ENDPOINT_URL": S3,
        "CORS_ORIGINS": UI, "JWT_SECRET": secrets.token_urlsafe(48),
        "WIFI_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "MONGO_ENSURE_INDEXES_ON_STARTUP": "true", "FAIL_INTERRUPTED_JOBS_ON_STARTUP": "true",
        "LOAD_MODEL_ON_STARTUP": "true", "DEVICE_SWEEPER_ENABLED": "true",
        "PROCESSING_WORKER_ENABLED": "true", "PROCESSING_WORKER_POLL_SECONDS": "1",
        "NUMBA_CACHE_DIR": str(runtime / "cache" / "numba"), "MPLCONFIGDIR": str(runtime / "cache" / "mpl"),
        "TORCH_HOME": str(runtime / "cache" / "torch"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "npm_config_offline": "true",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "PLAYWRIGHT_CHROMIUM_EXECUTABLE": str(args.chromium),
        "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
    }
    return env


def snapshot_source(destination):
    allowed = {"backend", "frontend", "model", "scripts", "infra"}
    raw = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    count = 0
    for name in set(os.fsdecode(raw).split("\0")) - {""}:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        if relative.parts[0] not in allowed and relative.as_posix() not in {
                "README.md", ".gitignore", "pyproject.toml", "pytest.ini", "requirements.txt"}:
            continue
        if any(part in {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", "data",
                        ".local_audio", ".local_edge", ".local_backups", ".local_bootstrap", ".local_evidence"} for part in relative.parts):
            continue
        if relative.as_posix() == "infra/aws/edge.env":
            continue
        if relative.name == ".env" or (relative.name.startswith(".env.") and not relative.name.endswith("example")):
            continue
        source = ROOT / relative
        if source.is_symlink():
            check(source.resolve().is_relative_to(ROOT), f"Refusing external source symlink: {relative}")
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        shutil.copymode(source, target)
        count += 1
    check(not (destination / "backend" / ".env").exists(), "Source snapshot unexpectedly includes an environment file")
    check(not (destination / "infra" / "aws" / "edge.env").exists(), "Source snapshot unexpectedly includes private edge settings")
    return count


def _child_setup():
    # Children are separate process groups for normal cleanup. Linux also kills a direct child
    # if this runner is killed abruptly; normal cleanup additionally reaps group descendants.
    parent = os.getppid()
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL)
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGKILL)


class Processes:
    def __init__(self, output, env):
        self.output, self.env = output, env
        self.active = []
        self.results = []

    def start(self, label, command, cwd, env=None):
        log = (self.output / "logs" / f"{label}.log").open("ab", buffering=0)
        try:
            process = subprocess.Popen([str(item) for item in command], cwd=cwd, env=env or self.env,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True, preexec_fn=_child_setup)
        except BaseException:
            log.close()
            raise
        job = {"label": label, "process": process, "log": log}
        self.active.append(job)
        return job

    def stop(self, job, timeout=15):
        if job not in self.active:
            return
        process = job["process"]
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
        # A successful parent can leave a browser/helper in its process group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        job["log"].close()
        self.active.remove(job)

    def run(self, label, command, cwd, env=None, timeout=900, expected=0):
        print(f"Running {label}...", flush=True)
        job = self.start(label, command, cwd, env)
        try:
            code = job["process"].wait(timeout=timeout)
            self.results.append({"check": label, "exit_code": code, "log": f"logs/{label}.log"})
            check(code == expected, f"{label} failed (exit {code}, expected {expected}); see logs/{label}.log")
        except subprocess.TimeoutExpired:
            raise CheckFailed(f"{label} timed out; see logs/{label}.log") from None
        finally:
            self.stop(job)

    def cleanup(self):
        errors = []
        for job in list(reversed(self.active)):
            try:
                self.stop(job)
            except Exception as exc:
                errors.append({"service": job["label"], "error_type": type(exc).__name__})
        return errors


def wait_for(label, condition, *, timeout=90, job=None):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if job is not None and job["process"].poll() is not None:
            raise CheckFailed(f"{label} exited; see logs/{job['label']}.log")
        try:
            if condition():
                return
        except (OSError, ValueError, URLError):
            pass
        time.sleep(0.4)
    raise CheckFailed(f"Timed out waiting for {label}")


def http(method, path, body=None, token=None, base=API):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      headers=headers, method=method)
    try:
        response = urlopen(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        data = response.read()
        try:
            data = json.loads(data)
        except ValueError:
            data = None
        return response.status, data


def junit_summary(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    sums = {name: sum(int(suite.get(name, 0)) for suite in suites)
            for name in ("tests", "failures", "errors", "skipped")}
    sums["passed"] = sums["tests"] - sums["failures"] - sums["errors"] - sums["skipped"]
    sums["skip_reasons"] = sorted({case.find("skipped").get("message", "")
                                 for case in root.iter("testcase") if case.find("skipped") is not None})
    return sums


def run_inside(args):
    os.umask(0o077)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    check(not any(output.iterdir()), "Output directory must be empty; choose a new evidence directory")
    (output / "logs").mkdir()
    report = {"status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "scope": "Disposable local QA; no AWS/Docker/CARLA; no external network route",
              "limitations": ["SES email is simulated", "Backups use DEV_FAKE_BACKUP, not pg_dump/mongodump",
                              "Five bundled scenario clips are inference regressions, not a dataset accuracy evaluation",
                              "No AWS deployment or credit eligibility verified"],
              "checks": []}
    report["mode"] = "record_only" if args.record_only else "application_only" if args.application_only else "full"
    if args.record_only:
        report["omitted_checks"] = {
            "reason": "--record-only is a focused video rehearsal, not a rerun of the full regression suite",
            "checks": ["backend unit tests", "PostgreSQL migration regression tests", "virtual edge route regression",
                       "live component API regression", "live platform API regression", "baseline UI regression",
                       "dedicated replay UI regression"],
            "retained_checks": ["kernel network isolation", "fresh owned stores and migrations", "private bootstrap",
                                "authenticated readiness and actual local S3 outage", "historical25 import and signed-byte proof",
                                "built frontend", "real replay edge", "recorder roles and five new replay flows",
                                "historical25 signed-byte proof after recording", "owned resource cleanup"]}
    processes = None
    code = 1
    private_values = []
    runtime = Path(tempfile.mkdtemp(prefix="autosound-local-qa-"))
    try:
        report["network_before"] = network_evidence(args.ip, args._host_netns)
        historical_audio = runtime / "historical-audio"
        if args.historical_audio_dir:
            report["historical_media_validation"] = stage_audio(args.historical_audio_dir, historical_audio)
            write_json(output / "historical-media-manifest.json", report["historical_media_validation"])
        check(os.getuid() != 0, "PostgreSQL requires the inner process to run as a non-root mapped user")
        source = runtime / "source"
        source.mkdir()
        report["snapshot_files"] = snapshot_source(source)
        report["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        report["source_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
        env = sanitized_env(runtime, args)
        env["PYTHONPATH"] = os.pathsep.join([str(source / "backend"), str(source)])
        env["MODEL_DIR"] = str(source / "model" / "artifacts")
        env["PLAYWRIGHT_MODULE"] = str(args.node_modules / "playwright")
        (source / "frontend" / "node_modules").symlink_to(args.node_modules, target_is_directory=True)
        processes = Processes(output, env)
        backend = source / "backend"
        py = args.python
        for label, command in (("python", [py, "--version"]),
                               ("postgres", [args.postgres_bin / "postgres", "--version"]),
                               ("mongodb", [args.mongod, "--version"]),
                               ("minio", [args.minio, "--version"]), ("node", [args.node, "--version"])):
            processes.run("version-" + label, command, runtime, timeout=30)
        report["versions"] = {name: (output / "logs" / f"version-{name}.log").read_text().splitlines()[0]
                              for name in ("python", "postgres", "mongodb", "minio", "node")}
        processes.run("python-dependencies", [py, "-c",
            "import importlib.metadata as m,json; print(json.dumps({name:m.version(name) for name in "
            "('torch','torchaudio','librosa','fastapi','pydantic','boto3','pymongo','sqlalchemy','pytest','httpx','soundfile')}))"],
            runtime, timeout=30)
        report["python_dependencies"] = json.loads((output / "logs" / "python-dependencies.log").read_text())
        check("PostgreSQL) 16." in report["versions"]["postgres"], "This harness requires PostgreSQL 16")
        check("v7." in report["versions"]["mongodb"], "This harness requires MongoDB 7")
        # Upstream removed model/orchestrator.py and its obsolete model/tests. The production
        # preprocessing/model tests live in backend/tests/test_ml.py; the five current bundled
        # clips are also checked against the exported weights by test_scenario_model.py.
        report["model_regression_scope"] = {
            "tests": ["backend/tests/test_ml.py", "backend/tests/test_scenario_model.py"],
            "purpose": "Production inference and five bundled clips; no training/accuracy claim"}
        # Unit tests have neither a real TEST_DATABASE_URL nor running stores. Their conftest
        # explicitly replaces application URLs with SQLite/mongomock configuration.
        unit_env = {**env, "APP_ENV": "test"}
        if args.application_only:
            report["unit_tests"] = {"status": "not_run", "reason": "--application-only; use separate backend test evidence"}
        else:
            processes.run("unit-tests", [py, "-m", "pytest", "-o", "addopts=", "-q", "-p", "no:cacheprovider",
                                          "backend/tests", f"--junitxml={output / 'unit-tests.xml'}"],
                          source, unit_env, timeout=1800)
            report["unit_tests"] = junit_summary(output / "unit-tests.xml")

        pgdata, pgsocket = runtime / "postgres", runtime / "pgsocket"
        pgsocket.mkdir()
        processes.run("postgres-init", [args.postgres_bin / "initdb", "-D", pgdata,
                                         "--username=qa", "--auth=trust", "--encoding=UTF8", "--no-locale"], runtime)
        pg = processes.start("postgres", [args.postgres_bin / "postgres", "-D", pgdata,
                                           "-h", "127.0.0.1", "-p", PG_PORT, "-k", pgsocket, "-F"], runtime)
        connection = ["--host=127.0.0.1", f"--port={PG_PORT}", "--username=qa"]

        def pg_ready():
            return subprocess.run([str(args.postgres_bin / "pg_isready"), *connection], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

        wait_for("PostgreSQL", pg_ready, job=pg)
        processes.run("migration-db-create", [args.postgres_bin / "createdb", *connection, DATABASE], runtime)
        migration_env = {**unit_env, "TEST_DATABASE_URL": env["DATABASE_URL"]}
        if args.application_only:
            report["postgres_migrations"] = {"status": "not_run", "reason": "--application-only; use separate migration test evidence"}
        else:
            processes.run("postgres-migrations", [py, "-m", "pytest", "-o", "addopts=", "-q", "-p", "no:cacheprovider",
                           "backend/tests/test_migration_pg.py", f"--junitxml={output / 'postgres-migrations.xml'}"],
                          source, migration_env)
            report["postgres_migrations"] = junit_summary(output / "postgres-migrations.xml")
        # Destructive migration tests touched only our fresh cluster. Recreate its owned DB so
        # neither their seed rows nor alembic state leak into the subsequent live checks.
        processes.run("live-db-drop", [args.postgres_bin / "dropdb", *connection, "--force", DATABASE], runtime)
        processes.run("live-db-create", [args.postgres_bin / "createdb", *connection, DATABASE], runtime)

        mongodata = runtime / "mongo"
        mongodata.mkdir()
        mg = processes.start("mongodb", [args.mongod, "--dbpath", mongodata, "--bind_ip", "127.0.0.1",
                                          "--port", MONGO_PORT, "--nounixsocket"], runtime)
        mongo_probe = [py, "-c", "import os,pymongo; pymongo.MongoClient(os.environ['MONGO_URI'],serverSelectionTimeoutMS=500).admin.command('ping')"]

        def mongo_ready():
            return subprocess.run([str(x) for x in mongo_probe], env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0

        wait_for("MongoDB", mongo_ready, job=mg)
        minio_command = [args.minio, "server", runtime / "minio", "--address", f"127.0.0.1:{S3_PORT}",
                         "--console-address", f"127.0.0.1:{CONSOLE_PORT}"]
        minio = processes.start("minio", minio_command, runtime)
        wait_for("MinIO", lambda: http("GET", "/minio/health/live", base=S3)[0] == 200, job=minio)
        processes.run("bucket-create", [py, "-c", "from app.db import s3; from app.config import get_settings; s3.get_client().create_bucket(Bucket=get_settings().S3_BUCKET,CreateBucketConfiguration={'LocationConstraint':'us-west-2'})"], backend)
        processes.run("live-migrations", [py, "-m", "alembic", "upgrade", "head"], backend)
        private_bundle = runtime / "private-bootstrap.json"
        bootstrap = [py, "-m", "app.db.bootstrap", "--initialize-empty-database", "--credentials-file", private_bundle]
        processes.run("private-bootstrap", bootstrap, backend)
        bootstrap_check = """
import bcrypt, hashlib, json, os, stat
from pathlib import Path
from sqlalchemy import select
from app.db import postgres, seed
from app.db.models import User, Device
bundle_path = Path(os.environ['LOCAL_QA_BOOTSTRAP_BUNDLE'])
assert stat.S_IMODE(bundle_path.stat().st_mode) == 0o600
bundle = json.loads(bundle_path.read_text())
assert len(bundle['users']) == 4 and len(bundle['devices']) == 5
public_passwords = {row['password'] for row in seed.USERS}
public_key_hashes = {seed.device_key_hash(row['name']) for row in seed.DEVICES}
with postgres.session_scope() as session:
    users = {row.id: row for row in session.scalars(select(User))}
    devices = {row.id: row for row in session.scalars(select(Device))}
    assert len(users) == 4 and len(devices) == 5
    for row in bundle['users']:
        assert len(row['password']) >= 32 and row['password'] not in public_passwords
        assert bcrypt.checkpw(row['password'].encode(), users[row['id']].password_hash.encode())
    for row in bundle['devices']:
        assert len(row['device_key']) >= 32
        assert devices[row['id']].device_key_hash == hashlib.sha256(row['device_key'].encode()).hexdigest()
        assert devices[row['id']].device_key_hash not in public_key_hashes
print('PASS private bootstrap: 4 matching private logins, 5 matching private device keys; no values printed')
"""
        bootstrap_env = {**env, "LOCAL_QA_BOOTSTRAP_BUNDLE": str(private_bundle)}
        processes.run("private-bootstrap-verify", [py, "-c", bootstrap_check], backend, bootstrap_env)
        before = private_bundle.read_bytes()
        processes.run("private-bootstrap-refuse-repeat", bootstrap, backend, expected=1)
        check(private_bundle.read_bytes() == before, "Repeated bootstrap replaced its private credential bundle")
        processes.run("private-bootstrap-preserved", [py, "-c", bootstrap_check], backend, bootstrap_env)
        report["private_bootstrap"] = {"status": "passed", "private_accounts": 4, "private_device_keys": 5,
                                       "bundle_mode": "0600", "repeat_refused": True, "bundle_preserved": True}
        write_json(output / "private-bootstrap-verification.json", report["private_bootstrap"])
        if args.private_bootstrap:
            bundle = json.loads(private_bundle.read_text())
            private_values.extend(row['password'] for row in bundle['users'])
            private_values.extend(row['device_key'] for row in bundle['devices'])
            env.update(credentials_env(bundle))
            report["private_bootstrap"]["retained_for_live_checks"] = True
        else:
            processes.run("bootstrap-db-drop", [args.postgres_bin / "dropdb", *connection, "--force", DATABASE], runtime)
            processes.run("bootstrap-db-create", [args.postgres_bin / "createdb", *connection, DATABASE], runtime)
            processes.run("demo-migrations", [py, "-m", "alembic", "upgrade", "head"], backend)
            processes.run("local-demo-seed", [py, "-m", "app.db.seed"], backend)
        write_json(output / "private-bootstrap-verification.json", report["private_bootstrap"])

        api = processes.start("api", [py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                       "--port", API_PORT, "--workers", "1", "--no-proxy-headers", "--no-access-log"], backend)
        wait_for("API", lambda: http("GET", "/health")[0] == 200, job=api)

        def login(email, password):
            status, body = http("POST", "/login", {"email": email, "password": password})
            check(status == 200 and body.get("access_token"), "Local seeded login failed")
            return body["access_token"]

        cloud = login(env.get("VERIFY_CLOUD_EMAIL", "cloud@autosound.dev"), env.get("VERIFY_CLOUD_PASSWORD", "cloud-dev-pass"))
        iot = login(env.get("VERIFY_IOT_EMAIL", "iot@autosound.dev"), env.get("VERIFY_IOT_PASSWORD", "iot-dev-pass"))
        wait_for("model and workers readiness", lambda: http("GET", "/admin/readiness", token=cloud)[0] == 200,
                 job=api, timeout=120)
        check(http("GET", "/admin/readiness")[0] == 401, "Readiness must reject anonymous requests")
        check(http("GET", "/admin/readiness", token=iot)[0] == 403, "Readiness must reject IoT Staff")
        ready_status, ready_body = http("GET", "/admin/readiness", token=cloud)
        report["readiness"] = {"anonymous": 401, "iot_staff": 403, "cloud_staff": ready_status,
                               "before_outage": ready_body}
        # Real local outage, not a mocked response. PostgreSQL remains live.
        processes.stop(minio)
        outage_status, outage_body = http("GET", "/admin/readiness", token=cloud)
        health_status, _ = http("GET", "/health")
        check(outage_status == 503 and outage_body["checks"]["s3"]["status"] == "unavailable",
              "Readiness must return 503 for an actual local S3 outage")
        check(health_status == 200, "Public health should stay 200 while PostgreSQL is available")
        report["readiness"].update({"minio_stopped": outage_status, "during_outage": outage_body,
                                    "health_during_s3_outage": health_status})
        minio = processes.start("minio-restored", minio_command, runtime)
        wait_for("restored MinIO", lambda: http("GET", "/minio/health/live", base=S3)[0] == 200, job=minio)
        wait_for("restored readiness", lambda: http("GET", "/admin/readiness", token=cloud)[0] == 200, job=api)
        report["readiness"]["restored"] = 200
        write_json(output / "readiness-verification.json", report["readiness"])

        history_command = [py, source / "scripts" / "verify_history_local.py", "--base-url", API,
                           "--object-origin", S3, "--audio-dir", historical_audio,
                           "--bundle", private_bundle]
        if args.historical_audio_dir:
            processes.run("historical-import-and-audio", [*history_command, "--apply-local", "--output",
                          output / "historical-verification.json"], backend)
            report["historical_before"] = json.loads((output / "historical-verification.json").read_text())

        edge_env = {**env, "EDGE_API_URL": API, "EDGE_CAR_ID": "1", "EDGE_DEVICE_ID": "1",
                    "EDGE_DEVICE_KEY": "dev-device-key-mic-1", "EDGE_CAMERA_DEVICE_ID": "5",
                    "EDGE_CAMERA_DEVICE_KEY": "dev-device-key-cam-1"}
        if args.private_bootstrap:
            edge_env.update({key: value for key, value in env.items() if key.startswith("EDGE_")})
        edge = None
        if not args.record_only:
            edge = processes.start("virtual-edge", [py, "-m", "edge.demo_client", "--no-audio-playback",
                                                "--buffer-dir", runtime / "edge-buffer"], backend, edge_env)
            wait_for("virtual edge and camera", lambda: (lambda result: result[0] == 200 and result[1].get("online")
                  and result[1].get("frame_url"))(http("GET", "/simulator/1/state", token=iot)), job=edge)
            processes.run("live-components", [py, source / "scripts" / "verify_components.py", "--base-url", API,
                                           "--output", output / "live-component-verification.json"], source)
            processes.run("live-platform", [py, source / "scripts" / "verify_platform_local.py", "--base-url", API,
                                        "--s3-endpoint-url", S3, "--s3-bucket", env["S3_BUCKET"],
                                        "--host-netns", args._host_netns,
                                        "--output", output / "platform-verification.json", "--component-evidence",
                                        output / "live-component-verification.json"], source)

        frontend = source / "frontend"
        vite = args.node_modules / "vite" / "bin" / "vite.js"
        browser_env = {**env, "VITE_USE_MOCK": "false", "VITE_API_BASE_URL": API,
                       "UI_BASE_URL": UI, "UI_EVIDENCE_DIR": str(output / "ui")}
        processes.run("frontend-build", [args.node, vite, "build", "--configLoader", "runner"], frontend,
                      browser_env, timeout=300)
        ui = processes.start("frontend", [args.node, vite, "preview", "--configLoader", "runner", "--host",
                                          "127.0.0.1", "--port", UI_PORT, "--strictPort"], frontend, browser_env)
        wait_for("built frontend", lambda: http("GET", "/", base=UI)[0] == 200, job=ui)
        if not args.record_only:
            processes.run("browser-components", [args.node, source / "scripts" / "verify_ui.cjs"], source,
                          browser_env, timeout=300)
        # No two producers may share the registered devices. Keep the virtual-car route
        # regression above, then independently exercise the default AWS recorded-replay feed.
        if edge is not None:
            processes.stop(edge)
        replay = processes.start("replay-edge", [py, "-m", "edge.replay_client", "--no-audio-playback",
                                                  "--buffer-dir", runtime / "replay-buffer"], backend, edge_env)
        wait_for("recorded CARLA edge and camera", lambda: (lambda result: result[0] == 200
                 and result[1].get("edge_mode") == "carla_replay" and result[1].get("frame_url"))(
                     http("GET", "/simulator/1/state", token=iot)), job=replay)
        replay_env = {**browser_env, "API_BASE_URL": API, "UI_EVIDENCE_DIR": str(output / "replay")}
        if not args.record_only:
            processes.run("browser-replay", [args.node, source / "scripts" / "verify_replay_ui.cjs"],
                          source, replay_env, timeout=420)
        if args.historical_audio_dir and not args.record_only:
            processes.run("historical-preserved-after-replay", [*history_command, "--replay-evidence",
                          output / "replay" / "replay-browser-verification.json", "--output",
                          output / "historical-after-replay.json"], backend)
            report["historical_after_replay"] = json.loads((output / "historical-after-replay.json").read_text())
        if args.record_demo:
            recording_env = {**replay_env, "RECORD_OUTPUT_DIR": str(output / "video"),
                             "UI_EVIDENCE_DIR": str(output / "video"),
                             "HISTORICAL_EVIDENCE_FILE": str(output / "historical-verification.json")}
            processes.run("record-private-demo", [args.node, source / "scripts" / "record_local_demo.cjs"],
                          source, recording_env, timeout=600)
            report["demo_recording"] = "video"
            processes.run("historical-preserved-after-recording", [*history_command, "--replay-evidence",
                          output / "video" / "recording-verification.json", "--output",
                          output / "historical-after-recording.json"], backend)
            report["historical_after_recording"] = json.loads((output / "historical-after-recording.json").read_text())
        report["network_after"] = network_evidence(args.ip, args._host_netns)
        report["status"] = "passed"
        code = 0
    except BaseException as exc:
        report["status"] = "failed"
        # Exceptions from HTTP/SDK libraries can contain URLs; no raw response, token or env dump.
        report["failure"] = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
        print("Local QA failed: " + report["failure"], file=sys.stderr, flush=True)
    finally:
        cleanup_errors = processes.cleanup() if processes is not None else []
        report["checks"] = processes.results if processes is not None else []
        report["cleanup"] = {"owned_processes_stopped": not cleanup_errors, "errors": cleanup_errors}
        if cleanup_errors:
            code, report["status"] = 1, "failed"
        try:
            shutil.rmtree(runtime)
            report["cleanup"]["temporary_stores_removed"] = True
        except OSError:
            code, report["status"] = 1, "failed"
            report["cleanup"]["temporary_stores_removed"] = False
        report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Diagnostic libraries can include authorization material in exception text.
        # Preserve useful evidence without retaining the temporary bootstrap credentials.
        for path in output.rglob('*'):
            if path.is_file() and path.suffix in {'.log', '.json', '.xml', '.txt'}:
                text = path.read_text(errors='replace')
                for value in private_values:
                    text = text.replace(value, '<private-bootstrap-secret>')
                text = re.sub(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}', '<jwt>', text)
                text = re.sub(r'(X-Amz-Signature=)[0-9a-fA-F]{64}', r'\1<signature>', text)
                path.write_text(text)
        write_json(output / "verification.json", report)
        print(f"Local QA {report['status']}; evidence: {output / 'verification.json'}", flush=True)
    return code


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--python", type=Path, required=True, help="existing backend virtualenv Python")
    parser.add_argument("--postgres-bin", type=Path, required=True, help="existing PostgreSQL 16 bin directory")
    parser.add_argument("--mongod", type=Path, required=True, help="existing MongoDB 7 server binary")
    parser.add_argument("--minio", type=Path, required=True, help="existing MinIO server binary")
    parser.add_argument("--node-modules", type=Path, required=True, help="existing frontend node_modules directory")
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or "/usr/bin/node"))
    parser.add_argument("--chromium", type=Path, required=True, help="installed Playwright Chromium executable")
    parser.add_argument("--output", type=Path, required=True, help="new/empty directory for JSON, JUnit, logs and screenshots")
    parser.add_argument("--application-only", action="store_true",
                        help="Re-run the application/UI on fresh stores, omitting backend unit/migration tests. Omissions are recorded; keep separate passing test evidence.")
    parser.add_argument("--private-bootstrap", action="store_true", help="Keep private bootstrap accounts/keys for all live checks; do not restore public development seed")
    parser.add_argument("--historical-audio-dir", type=Path, help="Private directory containing all 25 historical WAV files; implies --private-bootstrap")
    parser.add_argument("--record-demo", action="store_true", help="Record the private historical/replay UI before cleanup; requires --historical-audio-dir")
    parser.add_argument("--record-only", action="store_true", help="Focused fresh private video rehearsal; skip previously verified API/UI regressions and unit/migration tests; requires --historical-audio-dir")
    parser.add_argument("--_inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_host-netns", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    check(not args.record_only or args.historical_audio_dir is not None,
          "--record-only requires --historical-audio-dir")
    if args.record_only:
        args.application_only = args.private_bootstrap = args.record_demo = True
    check(not args.record_demo or args.historical_audio_dir is not None,
          "--record-demo requires --historical-audio-dir")
    if args.historical_audio_dir:
        args.historical_audio_dir = Path(os.path.abspath(args.historical_audio_dir))
        args.private_bootstrap = True
    for name in ("python", "postgres_bin", "mongod", "minio", "node_modules", "node", "chromium", "output"):
        # Do not resolve Python's final symlink: resolving bin/python can bypass its virtualenv.
        setattr(args, name, Path(os.path.abspath(getattr(args, name))))
    for binary in (args.python, args.mongod, args.minio, args.node, args.chromium,
                   *(args.postgres_bin / name for name in ("initdb", "postgres", "pg_isready", "createdb", "dropdb"))):
        check(binary.is_file() and os.access(binary, os.X_OK), f"Required executable missing: {binary}")
    for package in ("vite", "playwright"):
        check((args.node_modules / package).is_dir(), f"Required installed Node package missing: {package}")
    args.ip = shutil.which("ip")
    check(args.ip is not None, "iproute2 is required; no packages will be installed")
    return args


def main():
    try:
        args = parse_args()
        if args._inside:
            def interrupted(signum, frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, interrupted)
            return run_inside(args)
        check(sys.platform == "linux" and os.getuid() != 0, "Run as a regular Linux user with user namespaces enabled")
        unshare = shutil.which("unshare")
        check(unshare is not None, "unshare is required; isolation will not be bypassed")
        host_namespace = os.readlink("/proc/self/ns/net")
        # Outer namespace maps the caller to root just long enough to enable loopback. Inner
        # namespace remaps that identity to the caller's non-root UID/GID for PostgreSQL.
        shell = ('set -eu; qa_ip=$1; shift; "$qa_ip" link set lo up; '
                 'qa_unshare=$1; qa_uid=$2; qa_gid=$3; shift 3; '
                 'exec "$qa_unshare" --user --map-user="$qa_uid" --map-group="$qa_gid" -- "$@"')
        command = [unshare, "-Urn", "/bin/sh", "-c", shell, "local-qa", args.ip, unshare,
                   str(os.getuid()), str(os.getgid()), str(args.python), str(Path(__file__).absolute()),
                   *sys.argv[1:], "--_inside", "--_host-netns", host_namespace]
        print("Starting isolated local QA (loopback only; no external route, installs or downloads).", flush=True)
        process = subprocess.Popen(command, start_new_session=True)
        try:
            return process.wait()
        except KeyboardInterrupt:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return 130
    except CheckFailed as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
