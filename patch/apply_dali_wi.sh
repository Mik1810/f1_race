#!/bin/bash
# patch/apply_dali_wi.sh
# Applies all f1_race patches to DALI source files.
# Usage: bash apply_dali_wi.sh <dali_src_dir> <patch_dir>
# Idempotent: each file is skipped if the [F1_PATCH] marker is already present.

DALI_SRC="$1"   # e.g. /path/to/DALI/src
PATCH_DIR="$2"  # e.g. /path/to/f1_race/patch
LOG_FILE="$(dirname "$PATCH_DIR")/log/restart.log"

# ── Logging (mirrors _tick in startmas.sh, prefix [PATCH]) ─────────────────
_PT0=$(date +%s%3N)
_ptick() {
    local now; now=$(date +%s%3N)
    local elapsed=$(( now - _PT0 ))
    local msg; msg=$(printf "[PATCH][%6d ms] %s" "$elapsed" "$*")
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}
# ───────────────────────────────────────────────────────────────────────────

_ptick "START apply_patches (dali_src=$DALI_SRC)"

# ── Check python3 availability (needed for active_dali_wi.pl) ──────────────
if ! command -v python3 &>/dev/null; then
    _ptick "ERROR: python3 not found — cannot patch active_dali_wi.pl"
    echo "[patch] ERROR: python3 is required but not found in PATH." >&2
    exit 1
fi
_ptick "python3 found: $(python3 --version 2>&1)"

# ── Helper: patch a file by full replacement ────────────────────────────────
_patch_file() {
    local src="$1"
    local patched="$2"
    local name; name=$(basename "$src")
    if grep -q '\[F1_PATCH\]' "$src" 2>/dev/null; then
        _ptick "  $name — already patched, skipping"
    else
        _ptick "  $name — NOT patched, applying..."
        cp "$patched" "$src"
        _ptick "  $name — patch applied OK"
    fi
}

# ── Patch active_server_wi.pl and active_user_wi.pl (full replacement) ──────
_patch_file "$DALI_SRC/active_server_wi.pl" "$PATCH_DIR/active_server_wi.pl"
_patch_file "$DALI_SRC/active_user_wi.pl"   "$PATCH_DIR/active_user_wi.pl"

# ── Patch active_dali_wi.pl (surgical python patch — file is ~2200 lines) ───
FILE="$DALI_SRC/active_dali_wi.pl"
if grep -q '\[F1_PATCH\]' "$FILE" 2>/dev/null; then
    _ptick "  active_dali_wi.pl — already patched, skipping"
else
    _ptick "  active_dali_wi.pl — NOT patched, applying python patch..."
    python3 - "$FILE" <<'PYEOF'
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as f:
    content = f.read()

# Fix 1: assert(server_obj('localhost':3010)) → assert(server_obj(T))
old1 = "assert(server_obj('localhost':3010))"
new1 = "assert(server_obj(T))"
if old1 in content:
    content = content.replace(old1, new1, 1)
    print("[patch]   fixed assert(server_obj(...))")

# Fix 2: linda_client('localhost':3010) inside start0/1 → linda_client(T)
old2 = "linda_client('localhost':3010),\n            out(activating_agent(AgentName)),"
new2 = "linda_client(T),\n            out(activating_agent(AgentName)),"
if old2 in content:
    content = content.replace(old2, new2, 1)
    print("[patch]   fixed linda_client(...) in start0/1")

# Add [F1_PATCH] marker after the 6-line Apache licence header
marker = '% [F1_PATCH] read server address from server.txt (dynamic port support)'
if marker not in content:
    lines = content.split('\n')
    lines.insert(6, marker)
    content = '\n'.join(lines)
    print("[patch]   added [F1_PATCH] marker")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("[patch]   active_dali_wi.pl written OK")
PYEOF
    _ptick "  active_dali_wi.pl — python patch done"
fi

_ptick "END apply_patches"
