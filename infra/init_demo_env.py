"""Create private demo credentials once, preserving every existing nonempty value.

No third-party packages are needed and no credentials are printed.
"""
import argparse
import base64
import os
from pathlib import Path
import re
import secrets
import tempfile

ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
TEMPLATE = Path(__file__).with_name(".env.demo.example")
GENERATORS = {
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(32),
    "MINIO_ROOT_USER": lambda: "demo" + secrets.token_hex(10),
    "MINIO_ROOT_PASSWORD": lambda: secrets.token_urlsafe(32),
    "JWT_SECRET": lambda: secrets.token_urlsafe(48),
    "WIFI_ENCRYPTION_KEY": lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
}


def initialize(target: Path) -> bool:
    if target.is_symlink():
        raise ValueError("Refusing to replace a symbolic-link environment file")
    template_lines = TEMPLATE.read_text().splitlines()
    defaults = {match.group(1): match.group(2) for line in template_lines
                if (match := ASSIGNMENT.match(line))}
    current = target.read_text() if target.exists() else ""
    lines = current.splitlines() if current else template_lines
    seen, output = set(), []
    for line in lines:
        match = ASSIGNMENT.match(line)
        if match:
            key, value = match.groups()
            if key in seen:
                raise ValueError(f"Duplicate environment key: {key}")
            seen.add(key)
            if key in defaults and not value.strip().strip("\"'"):
                value = GENERATORS[key]() if key in GENERATORS else defaults[key]
                line = f"{key}={value}"
        output.append(line)
    for key, default in defaults.items():
        if key not in seen:
            output.append(f"{key}={GENERATORS[key]() if key in GENERATORS else default}")
    text = "\n".join(output) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    if text == current:
        target.chmod(0o600)
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=".demo-env-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name(".env.demo"))
    args = parser.parse_args()
    try:
        changed = initialize(args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print("Demo environment initialized; existing values preserved. Secrets were not printed."
          if changed else "Demo environment already configured; existing values preserved.")


if __name__ == "__main__":
    main()
