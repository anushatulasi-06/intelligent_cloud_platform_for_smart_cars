"""Local regressions: no AWS credentials, Docker daemon, network, or deployment required."""
import base64
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

AWS = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


config = module("aws_local_config", AWS / "validate_production_env.py")
audio = module("aws_local_audio", AWS / "seed_audio_manifest.py")


def valid_settings():
    return {
        "APP_ENV": "production", "DATABASE_URL": "postgresql://test:test@localhost/test",
        "MONGO_URI": "mongodb://localhost/test", "S3_BUCKET": "local-test-only",
        "AWS_REGION": "us-west-2", "JWT_SECRET": "local-test-JWT-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        "WIFI_ENCRYPTION_KEY": base64.urlsafe_b64encode(bytes(range(32))).decode(),
        "CORS_ORIGINS": "http://127.0.0.1:5173", "SES_FROM_EMAIL": "test@example.invalid",
        "DEV_FAKE_EMAIL": "false", "DEV_FAKE_BACKUP": "false", "S3_ENDPOINT_URL": "",
        "S3_PUBLIC_ENDPOINT_URL": "",
    }


def write_settings(path, values):
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))


class ProductionSettingsTests(unittest.TestCase):
    def test_private_settings_and_https_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "settings"
            for origin in ["http://127.0.0.1:5173", "http://localhost:5173", "https://demo.example.invalid"]:
                settings = valid_settings(); settings["CORS_ORIGINS"] = origin
                write_settings(file, settings)
                self.assertEqual(config.validate(file), [])

    def test_unsafe_settings_fail_without_exposing_values(self):
        cases = [("JWT_SECRET", ""), ("JWT_SECRET", "dev-only-secret-change-me-before-deploying"),
                 ("JWT_SECRET", "default-signing-secret-abcdefghijklmnopqrstuvwxyz"),
                 ("WIFI_ENCRYPTION_KEY", ""), ("WIFI_ENCRYPTION_KEY", "invalid-private-value"),
                 ("WIFI_ENCRYPTION_KEY", base64.urlsafe_b64encode(bytes(32)).decode()),
                 ("CORS_ORIGINS", "http://public.example.invalid"), ("CORS_ORIGINS", "*"),
                 ("CORS_ORIGINS", "http://127.0.0.1:5173/"), ("APP_ENV", "development"),
                 ("DEV_FAKE_EMAIL", "true"), ("S3_BUCKET", "<bucket>")]
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "settings"
            for key, value in cases:
                with self.subTest(key=key, value=value):
                    settings = valid_settings(); settings[key] = value
                    write_settings(file, settings)
                    errors = config.validate(file)
                    self.assertTrue(errors)
                    if value and len(value) > 8:
                        self.assertNotIn(value, " ".join(errors))

    def test_duplicate_settings_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "settings"
            write_settings(file, valid_settings())
            with file.open("a") as stream: stream.write("APP_ENV=production\n")
            self.assertTrue(any("Duplicate" in error for error in config.validate(file)))

    def test_edge_template_is_not_a_working_public_credential(self):
        self.assertTrue(config.validate_edge(AWS / "edge.env.example"))
        text = (AWS / "edge.env.example").read_text()
        self.assertIn("--demo-only", text)
        self.assertNotIn("=dev-device-key-", text)

    def test_edge_selector_accepts_only_replay_or_virtual_demo(self):
        settings = {"EDGE_CAR_ID": "1", "EDGE_DEVICE_ID": "1", "EDGE_CAMERA_DEVICE_ID": "5",
                    "EDGE_DEVICE_KEY": "private-test-microphone-key-abcdefghijklmnopqrstuvwxyz",
                    "EDGE_CAMERA_DEVICE_KEY": "private-test-camera-key-abcdefghijklmnopqrstuvwxyz",
                    "DEMO_DATA_ARGS": "--demo-only --live --offline 1 4"}
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "edge-settings"
            for selector in [None, "", "replay_client", "demo_client"]:
                with self.subTest(selector=selector):
                    values = dict(settings)
                    if selector is not None: values["EDGE_CLIENT"] = selector
                    write_settings(file, values)
                    self.assertEqual(config.validate_edge(file), [])
            for selector in ["carla_demo", "unknown", "replay_client; echo unexpected"]:
                with self.subTest(selector=selector):
                    write_settings(file, {**settings, "EDGE_CLIENT": selector})
                    self.assertIn("EDGE_CLIENT must be replay_client or demo_client.", config.validate_edge(file))


class DeploymentGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(AWS, self.root / "infra/aws", ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "backend").mkdir()
        self.bin = self.root / "bin"; self.bin.mkdir()
        self.marker = self.root / "external-command-ran"
        for name in ["sudo", "aws", "curl", "dnf", "pip", "git", "docker", "npm", "systemctl"]:
            stub = self.bin / name
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$TEST_MARKER"\nexit 99\n')
            stub.chmod(0o755)
        self.env = {"PATH": f"{self.bin}:/usr/bin:/bin", "TEST_MARKER": str(self.marker),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, name, additions=None):
        env = dict(self.env); env.update(additions or {})
        return subprocess.run(["/bin/bash", str(self.root / "infra/aws" / name)], env=env,
                              text=True, capture_output=True, timeout=5)

    def test_every_entry_point_fails_before_commands_or_template_writes(self):
        scripts = ["setup_ec2.sh", "deploy_backend.sh", "deploy_frontend.sh",
                   "docker/setup_ec2_docker.sh", "docker/compose.sh"]
        for script in scripts:
            for consent in [{}, {"ALLOW_AWS_DEPLOYMENT": "I_ACCEPT_AWS_RESOURCE_CHANGES",
                                  "AWS_COST_ACK": "I_ACCEPT_AWS_USAGE_COSTS", "AWS_CREDIT_BUDGET_USD": "0"}]:
                with self.subTest(script=script, consent=bool(consent)):
                    result = self.run_script(script, consent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("blocked", result.stderr.lower())
                    self.assertFalse(self.marker.exists())
                    self.assertFalse((self.root / "backend/.env").exists())

    def test_invalid_secrets_block_before_install_even_with_cost_consent(self):
        write_settings(self.root / "backend/.env", {**valid_settings(), "JWT_SECRET": ""})
        consent = {"ALLOW_AWS_DEPLOYMENT": "I_ACCEPT_AWS_RESOURCE_CHANGES",
                   "AWS_COST_ACK": "I_ACCEPT_AWS_USAGE_COSTS", "AWS_CREDIT_BUDGET_USD": "1"}
        for script in ["setup_ec2.sh", "deploy_backend.sh", "docker/setup_ec2_docker.sh", "docker/compose.sh"]:
            result = self.run_script(script, consent)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("JWT_SECRET", result.stdout)
            self.assertFalse(self.marker.exists())

    def test_demo_seed_and_unacknowledged_feeds_cannot_run(self):
        consent = {"ALLOW_AWS_DEPLOYMENT": "I_ACCEPT_AWS_RESOURCE_CHANGES",
                   "AWS_COST_ACK": "I_ACCEPT_AWS_USAGE_COSTS", "AWS_CREDIT_BUDGET_USD": "1"}
        for flags in [{"SKIP_SEED": "0"}, {"ENABLE_EDGE_DEMO": "1"}, {"ENABLE_DEMO_DATA": "1"}]:
            result = self.run_script("setup_ec2.sh", {**consent, **flags})
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.marker.exists())

    def test_compose_helper_preserves_consent_through_sudo(self):
        capture = self.bin / "sudo"
        capture.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_MARKER"\n')
        capture.chmod(0o755)
        env = {**self.env, "ALLOW_AWS_DEPLOYMENT": "I_ACCEPT_AWS_RESOURCE_CHANGES",
               "AWS_COST_ACK": "I_ACCEPT_AWS_USAGE_COSTS", "AWS_CREDIT_BUDGET_USD": "1"}
        subprocess.run(["/bin/bash", "-c", 'source "$1"; run_aws_compose stop edge-demo',
                        "test", str(AWS / "deployment_guard.sh")], env={**env, "REPO_DIR": str(self.root)},
                       check=True, timeout=5)
        arguments = self.marker.read_text()
        self.assertIn("AWS_CREDIT_BUDGET_USD=1", arguments)
        self.assertIn("AWS_COST_ACK=I_ACCEPT_AWS_USAGE_COSTS", arguments)
        self.assertIn("stop\nedge-demo", arguments)

    def test_authorized_docker_control_flow_keeps_feeds_off_and_forwards_acknowledgements(self):
        # All external programs are local fakes. Exercise the real wrapper's stop/up flow,
        # including sudo's environment boundary; never contact a daemon or install anything.
        write_settings(self.root / "backend/.env", valid_settings())
        (self.bin / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
        (self.bin / "systemctl").write_text('#!/bin/sh\nexit 0\n')
        (self.bin / "curl").write_text('#!/bin/sh\nprintf "{}"\n')
        # Test the x86 AWS target independently of the developer's host (e.g. Apple Silicon).
        # The real installer must keep rejecting ARM because its backup image is amd64-only.
        (self.bin / "uname").write_text('#!/bin/sh\nprintf "x86_64\\n"\n')
        (self.bin / "uname").chmod(0o755)
        (self.bin / "docker").write_text(
            '#!/bin/sh\nprintf "%s|%s|%s|%s|%s\\n" "$*" "$ALLOW_AWS_DEPLOYMENT" '
            '"$AWS_COST_ACK" "$AWS_CREDIT_BUDGET_USD" "$COMPOSE_PROFILES" >> "$TEST_MARKER"\n')
        consent = {"ALLOW_AWS_DEPLOYMENT": "I_ACCEPT_AWS_RESOURCE_CHANGES",
                   "AWS_COST_ACK": "I_ACCEPT_AWS_USAGE_COSTS", "AWS_CREDIT_BUDGET_USD": "1"}
        result = self.run_script("docker/setup_ec2_docker.sh", consent)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.marker.read_text().splitlines()
        controls = [line for line in calls if any(word in line for word in ["stop edge-demo", "stop demo-data", "up --build -d"])]
        self.assertEqual(len(controls), 3)
        for call in controls:
            self.assertTrue(call.endswith("|I_ACCEPT_AWS_RESOURCE_CHANGES|I_ACCEPT_AWS_USAGE_COSTS|1|"), call)
        self.assertFalse((self.root / "infra/aws/edge.env").exists())

    def test_arm_target_is_rejected_before_any_install_or_start(self):
        write_settings(self.root / "backend/.env", valid_settings())
        (self.bin / "uname").write_text('#!/bin/sh\nprintf "aarch64\\n"\n')
        (self.bin / "uname").chmod(0o755)
        consent = {"ALLOW_AWS_DEPLOYMENT": "I_ACCEPT_AWS_RESOURCE_CHANGES",
                   "AWS_COST_ACK": "I_ACCEPT_AWS_USAGE_COSTS", "AWS_CREDIT_BUDGET_USD": "1"}
        result = self.run_script("docker/setup_ec2_docker.sh", consent)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Use an x86_64 instance", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_unknown_edge_selector_blocks_both_installers_before_external_commands(self):
        write_settings(self.root / "backend/.env", valid_settings())
        write_settings(self.root / "infra/aws/edge.env", {
            "EDGE_CLIENT": "unknown", "EDGE_CAR_ID": "1", "EDGE_DEVICE_ID": "1",
            "EDGE_CAMERA_DEVICE_ID": "5", "EDGE_DEVICE_KEY": "private-test-mic-key-abcdefghijklmnopqrstuvwxyz",
            "EDGE_CAMERA_DEVICE_KEY": "private-test-cam-key-abcdefghijklmnopqrstuvwxyz",
            "DEMO_DATA_ARGS": "--demo-only --live --offline 1 4",
        })
        consent = {"ALLOW_AWS_DEPLOYMENT": "I_ACCEPT_AWS_RESOURCE_CHANGES",
                   "AWS_COST_ACK": "I_ACCEPT_AWS_USAGE_COSTS", "AWS_CREDIT_BUDGET_USD": "1",
                   "ENABLE_EDGE_DEMO": "1", "ALLOW_AWS_DEMO_FEEDS": "I_UNDERSTAND_REQUEST_COSTS"}
        for installer in ["setup_ec2.sh", "docker/setup_ec2_docker.sh"]:
            result = self.run_script(installer, consent)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("EDGE_CLIENT must be replay_client or demo_client.", result.stdout)
            self.assertFalse(self.marker.exists())

    def test_ports_and_bootstrap_volume_are_private(self):
        text = (AWS / "docker/docker-compose.yml").read_text()
        self.assertIn('"127.0.0.1:8000:8000"', text)
        self.assertIn('"127.0.0.1:80:8080"', text)
        self.assertIn("private-bootstrap:/app/backend/.local_bootstrap", text)
        self.assertNotIn("python -m app.db.seed", text)
        self.assertIn("--host 127.0.0.1", (AWS / "autosound-api.service").read_text())

    def test_api_restart_requires_consent_and_is_scoped(self):
        write_settings(self.root / "backend/.env", valid_settings())
        script = self.root / "infra/aws/docker/compose.sh"
        consent = {"ALLOW_AWS_DEPLOYMENT": "I_ACCEPT_AWS_RESOURCE_CHANGES",
                   "AWS_COST_ACK": "I_ACCEPT_AWS_USAGE_COSTS", "AWS_CREDIT_BUDGET_USD": "1"}
        # The sudo fake records argv instead of reaching Docker or restarting any service.
        (self.bin / "sudo").write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$TEST_MARKER"\n')
        for arguments, variables, allowed in [(["restart", "api"], {}, False),
                                              (["restart", "api"], {**consent, "AWS_CREDIT_BUDGET_USD": "0"}, False),
                                              (["restart", "frontend"], consent, False),
                                              (["restart", "api", "frontend"], consent, False),
                                              (["restart", "api"], consent, True)]:
            with self.subTest(arguments=arguments, allowed=allowed):
                result = subprocess.run(["/bin/bash", str(script), *arguments],
                                        env={**self.env, **variables}, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
                self.assertEqual(self.marker.exists(), allowed)
                if allowed:
                    call = self.marker.read_text()
                    self.assertIn("restart\napi", call)
                    self.assertIn("AWS_CREDIT_BUDGET_USD=1", call)


class AudioManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.alerts = self.root / "alerts.json"
        self.alerts.write_text(json.dumps([{"s3_object_key": "audio/1/test.wav"}]))
        self.clips = self.root / "clips"; self.clips.mkdir()
        self.wav = self.clips / "test.wav"
        with wave.open(str(self.wav), "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes(bytes(320))

    def tearDown(self): self.temp.cleanup()

    def test_manifest_covers_keys_and_detects_changed_bytes(self):
        manifest = audio.build(self.clips, self.alerts)
        audio.verify(manifest, self.alerts)
        self.assertEqual(manifest["objects"][0]["key"], "audio/1/test.wav")
        self.wav.write_bytes(self.wav.read_bytes() + b"changed")
        with self.assertRaises(ValueError): audio.verify(manifest, self.alerts)

    def test_missing_duplicate_symlink_and_truncated_audio_are_rejected(self):
        nested = self.clips / "nested"; nested.mkdir()
        duplicate = nested / "test.wav"; shutil.copyfile(self.wav, duplicate)
        with self.assertRaises(ValueError): audio.build(self.clips, self.alerts)
        duplicate.unlink()
        self.wav.unlink()
        with self.assertRaises(ValueError): audio.build(self.clips, self.alerts)
        self.wav.symlink_to(self.alerts)
        with self.assertRaises(ValueError): audio.build(self.clips, self.alerts)
        self.wav.unlink(); self.wav.write_bytes(b"RIFFbad")
        with self.assertRaises(ValueError): audio.build(self.clips, self.alerts)

    def test_traversal_key_is_rejected(self):
        self.alerts.write_text(json.dumps([{"s3_object_key": "audio/../unsafe.wav"}]))
        with self.assertRaises(ValueError): audio.expected_keys(self.alerts)

    def test_manifest_creation_is_private_from_first_open(self):
        # Run the real CLI with tiny local fixtures for the tracked keys under a permissive
        # umask. The output must already be private when created, without a chmod repair.
        for key in audio.expected_keys():
            shutil.copyfile(self.wav, self.clips / Path(key).name)
        file = self.root / "manifest.json"
        result = subprocess.run(["/bin/bash", "-c", 'umask 000; exec python3 "$1" --directory "$2" --output "$3"',
                                 "test", str(AWS / "seed_audio_manifest.py"), str(self.clips), str(file)],
                                capture_output=True, text=True, timeout=10,
                                env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assertEqual(len(json.loads(file.read_text())["objects"]), len(audio.expected_keys()))


if __name__ == "__main__":
    unittest.main()
