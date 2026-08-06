#!/usr/bin/env bash
#
# Everything a fresh machine needs. Safe to re-run.
#
# Called by the devcontainer on create, and usable directly:
#
#     bash .devcontainer/setup.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "Installing the kernel (no third-party dependencies at all)"
# Best effort. A distribution-managed pip refuses to uninstall itself
# ("RECORD file not found. Hint: The package was installed by debian"), and a
# setup script that dies on a cosmetic upgrade is a setup script people stop
# trusting.
python -m pip install --upgrade pip --quiet 2>/dev/null || \
    echo "  (leaving the system pip alone)"
python -m pip install -e . --quiet

say "Installing the interactive layer"
python -m pip install -r requirements-notebooks.txt --quiet

say "Registering the Jupyter kernel"
python -m ipykernel install --user --name python3 --display-name "Python 3 (Macropol-s)" >/dev/null 2>&1 || true

say "Checking the notebooks are in sync with their spec"
python -m tools.notebooks.build --check

say "Verifying the install"
python - <<'PY'
import slpie, gratimos
from slpie.compose import registry

verbs = registry()
print(f"  slpie    {slpie.__version__}  ·  {len(verbs.names)} verbs in {len(verbs.groups())} groups")
print(f"  gratimos {gratimos.__version__}")
PY

cat <<'EOF'

Ready.

  jupyter lab notebooks/          the notebooks — start with 00_start_here
  slpie help                      every verb, generated from the registry
  slpie demo                      the narrated end-to-end run
  make test                       2404 tests, no network
  make notebooks-run              execute every notebook and fail on any error

EOF
