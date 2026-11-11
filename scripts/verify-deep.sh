#!/usr/bin/env bash
# Focused, fail-closed V25 Deep-mode source and native validation.
# Receipt is kept outside the source tree so source integrity stays unchanged.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source_root="$(cd -- "${script_dir}/.." && pwd -P)"
receipt="${1:-$(dirname -- "${source_root}")/Craft_Anything_V25_Deep_Gate_$(date -u +%Y%m%dT%H%M%SZ)_$$_${RANDOM}.json}"

exec python3 "${script_dir}/native_validation.py" --root "${source_root}" --profile deep --report "${receipt}"
