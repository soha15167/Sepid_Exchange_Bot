"""Atomically update only receipt-recognition runtime settings in an env file."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


SETTINGS = {
    "BANKING_GEMINI_MODEL": "gemini-3.6-flash",
    "BANKING_GEMINI_MODEL_FALLBACKS": "gemini-3.5-flash-lite,gemini-3.1-flash-lite",
    "BANKING_GEMINI_TIMEOUT_SEC": "20",
    "BANKING_GEMINI_MAX_RETRIES": "1",
    "BANKING_GOOGLE_VISION_ENABLED": "1",
    "BANKING_GOOGLE_VISION_TIMEOUT_SEC": "15",
    "BANKING_GOOGLE_VISION_LANGUAGE_HINTS": "fa,en",
    "GOOGLE_APPLICATION_CREDENTIALS": "/etc/sepid/google-vision.json",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file")
    args = parser.parse_args()
    path = Path(args.env_file).resolve()
    original = path.read_text(encoding="utf-8")
    seen: set[str] = set()
    output: list[str] = []
    for line in original.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in SETTINGS:
            if key not in seen:
                output.append(f"{key}={SETTINGS[key]}")
                seen.add(key)
            continue
        output.append(line)
    for key, value in SETTINGS.items():
        if key not in seen:
            output.append(f"{key}={value}")

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, path.stat().st_mode & 0o777)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
