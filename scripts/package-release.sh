#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${RELEASE_PYTHON:-python3}"
package_output=$("$python_bin" scripts/package_release.py "$@")
printf '%s\n' "$package_output"
validated=0
while IFS= read -r asset; do
  if [[ "$asset" == */venus-os-observability-*.tar.gz ]]; then
    "$python_bin" scripts/validate_setuphelper_archive.py "$asset"
    validated=1
  fi
done <<< "$package_output"
if [[ "$validated" != 1 ]]; then
  echo 'No native SetupHelper archive was available for validation.' >&2
  exit 1
fi
