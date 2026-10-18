#!/usr/bin/env bash
# Apply the harness's two edits to an installed FLE (D61). Run after every install:
#
#     scripts/patch_fle.sh ~/venvs/fle
#
# Both live in site-packages, so every reinstall of FLE silently undoes them:
#
# 1. Pin the Factorio server image to 2.0.77, so a current game client can join (D34).
#    FLE ships factoriotools/factorio:2.0.73; multiplayer refuses any version mismatch,
#    and Steam offers 2.0.77 but not 2.0.73.
# 2. Stop five tools truncating their own error messages to the text after the last
#    colon (D37). Upstream fixed the shared tool.py in 0.4.8 (#398), but not these five:
#    "No lab in inventory. Current inventory: coal=5" still reaches the agent as
#    "Could not place lab at (x, y), coal=5".
#
# Idempotent: an already-patched file is left alone, and the first run keeps a .orig
# copy of each file it changes. Exits non-zero if either edit is incomplete afterwards.
#
# fle/eval/inspect/sandbox/Dockerfile also names 2.0.73; nothing in the harness builds
# it, so it is left alone.

set -euo pipefail

VENV="${1:?usage: scripts/patch_fle.sh <venv path>}"
WANT="2.0.77"

FLE_DIR="$("$VENV/bin/python" -c 'import fle, os; print(os.path.dirname(fle.__file__))')"
CLUSTER="$FLE_DIR/cluster"

# run-envs.sh is the one that matters: it regenerates docker-compose.yml on every start.
# docker-compose.yml does not exist in a fresh install - the first start writes it - so
# it is patched only if present.
FILES=(
    "$CLUSTER/run-envs.sh"
    "$CLUSTER/run_envs.py"
)
[[ -f "$CLUSTER/docker-compose.yml" ]] && FILES+=("$CLUSTER/docker-compose.yml")

# --- 1. Factorio version pin (D34) ------------------------------------------------------
for f in "${FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "missing: $f - FLE's cluster layout has changed; update this script" >&2
        exit 1
    fi
    if ! grep -Eo "factoriotools/factorio:[0-9]+\.[0-9]+\.[0-9]+" "$f" | grep -vq "factorio:$WANT"; then
        echo "already $WANT: $f"
        continue
    fi
    [[ -f "$f.orig" ]] || cp "$f" "$f.orig"
    sed -Ei "s#factoriotools/factorio:[0-9]+\.[0-9]+\.[0-9]+#factoriotools/factorio:$WANT#g" "$f"
    echo "pinned $WANT: $f"
done

# Verify: every image reference in the cluster files names the wanted version.
if grep -En "factoriotools/factorio:[0-9]+\.[0-9]+\.[0-9]+" "${FILES[@]}" | grep -v "factorio:$WANT"; then
    echo "some image references are not $WANT (above)" >&2
    exit 1
fi
if ! grep -q "factoriotools/factorio:$WANT" "${FILES[@]}"; then
    echo "no image reference found in the cluster files; update this script" >&2
    exit 1
fi
echo "ok: FLE's cluster runs factoriotools/factorio:$WANT"

# --- 2. Error truncation (D37) ----------------------------------------------------------
"$VENV/bin/python" - "$FLE_DIR" <<'PY'
import os
import shutil
import sys

root = sys.argv[1]
TRUNCATION = '.split(":")[-1]'
TARGETS = [
    "env/tools/tool.py",  # fixed upstream in 0.4.8; kept so an older FLE is still caught
    "env/tools/agent/harvest_resource/client.py",
    "env/tools/agent/get_resource_patch/client.py",
    "env/tools/agent/insert_item/client.py",
    "env/tools/agent/set_entity_recipe/client.py",
    "env/tools/agent/place_entity/client.py",
]
for relative in TARGETS:
    path = os.path.join(root, relative)
    if not os.path.exists(path):
        sys.exit(f"missing: {path} - FLE's tool layout has changed; update this script")
    text = open(path, encoding="utf-8").read()
    if TRUNCATION not in text:
        print("already untruncated:", relative)
        continue
    if not os.path.exists(path + ".orig"):
        shutil.copyfile(path, path + ".orig")
    open(path, "w", encoding="utf-8").write(text.replace(TRUNCATION, ""))
    print("untruncated:", relative)
PY

# Verify across every tool, not just the known six: a new site would be a new bug.
if grep -rn --include='*.py' 'split(":")\[-1\]' "$FLE_DIR/env/tools"; then
    echo "error messages are still truncated at the sites above" >&2
    exit 1
fi
echo "ok: no tool truncates its error messages"
