#!/bin/bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <telemt-version> [compose-service]"
    echo "Example: $0 3.4.3 awg-jump"
    exit 1
fi

VERSION="$1"
SERVICE="${2:-awg-jump}"
ENV_FILE="${ENV_FILE:-.env}"

if [ ! -f "$ENV_FILE" ]; then
    echo "Env file not found: $ENV_FILE"
    exit 1
fi

python3 - "$ENV_FILE" "$VERSION" <<'PYEOF'
from pathlib import Path
import sys

path = Path(sys.argv[1])
version = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
updated = False
rendered: list[str] = []

for line in lines:
    if line.startswith("TELEMT_VERSION="):
        rendered.append(f"TELEMT_VERSION={version}")
        updated = True
    else:
        rendered.append(line)

if not updated:
    if rendered and rendered[-1].strip():
        rendered.append("")
    rendered.append(f"TELEMT_VERSION={version}")

path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
PYEOF

docker compose build --build-arg TELEMT_VERSION="$VERSION" "$SERVICE"
docker compose up -d "$SERVICE"

echo "TeleMT updated to version $VERSION"
