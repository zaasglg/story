"""Generate a new deployment's private environment; never overwrite existing keys."""
import argparse
import base64
import json
import os
import re
import secrets
from pathlib import Path


def generate(domain: str, output: Path) -> None:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain) or "." not in domain:
        raise ValueError("Pass a domain name without scheme, path or port")
    values = {
        "SERVER_DOMAIN": domain.lower(),
        "POSTGRES_PASSWORD": secrets.token_hex(32),
        "M1917_JWT_SECRET": secrets.token_urlsafe(48),
        "M1917_OTP_PEPPER": secrets.token_urlsafe(48),
        "M1917_PII_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "M1917_CORS_ORIGINS": json.dumps([f"https://{domain.lower()}"], separators=(",", ":")),
        "M1917_ATTENDANCE_AUTO_FROM": "",
        "M1917_ATTENDANCE_GRACE_MINUTES": "30",
    }
    content = "# Private deployment keys: keep a separate protected backup. Do not commit.\n"
    content += "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as target:
        target.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--output", type=Path, default=Path(".env.server"))
    args = parser.parse_args()
    generate(args.domain, args.output)
    print(f"Created {args.output}; keys were not printed. Keep this file outside Git.")
