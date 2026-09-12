#!/bin/sh
#
# venus-os-observability self-update script.
#
# Ships inside the package and runs ON the Venus OS device to install
# the release into INSTALL_DIR (default /data/venus-os-observability).
# Invoked by SetupHelper PackageManager (via setup) or manually:
#
#     sh update.sh [INSTALL_DIR]
#
# This script owns layout knowledge (runtime files, daemontools services,
# /service symlinks, /data/rc.local persistence, legacy /data/opt migration).
#
set -eu

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${1:-/data/venus-os-observability}"
LEGACY_OPT="/data/opt/venus-os-observability"
SVC_NAME="venus-os-observability"

# Runtime items shipped at the repo root and installed at INSTALL_DIR root.
RUNTIME_ITEMS="src services version setup update.sh gitHubInfo pyproject.toml setup.py config.example.yaml"

sep() { echo "=== venus-os-observability update: $*"; }

# The service and boot hook use the standard persistent package path.
if [ "$INSTALL_DIR" != /data/venus-os-observability ]; then
    echo "Unsupported install directory: $INSTALL_DIR (expected /data/venus-os-observability)" >&2
    exit 1
fi

# Check dependencies before interrupting a healthy service. Native dbus-python
# and GLib come from Venus OS; create venvs with --system-site-packages.
PYTHON=python3
for candidate in "$INSTALL_DIR/.venv2/bin/python" "$INSTALL_DIR/.venv/bin/python" \
    "$LEGACY_OPT/.venv2/bin/python" "$LEGACY_OPT/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
        PYTHON="$candidate"
        break
    fi
done
PYTHONPATH="$SRC_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -c \
    'import dbus; from gi.repository import GLib; import venus_observability.__main__' || {
    echo "Runtime dependencies missing; bootstrap offline wheels before installing." >&2
    exit 1
}

# Keep service directory inodes and supervisors stable across updates. Never
# kill processes by working directory: that can kill this installer or an SSH
# shell, and unrelated processes may share the package directory.
if [ -e "/service/$SVC_NAME" ]; then
    svc -d "/service/$SVC_NAME"
    sleep 2
    svc -k "/service/$SVC_NAME" 2>/dev/null || true
fi

mkdir -p "$INSTALL_DIR"
sep "installing from $SRC_DIR into $INSTALL_DIR"

# 2. Install runtime items when source != install (PackageManager already
#    extracted into INSTALL_DIR; copying onto itself would rm the source).
if [ "$SRC_DIR" != "$INSTALL_DIR" ]; then
    for item in $RUNTIME_ITEMS; do
        [ -n "$item" ] || continue
        if [ -e "$SRC_DIR/$item" ]; then
            rm -rf "${INSTALL_DIR:?}/$item"
            cp -a "$SRC_DIR/$item" "$INSTALL_DIR/$item"
        fi
    done
else
    sep "source is install dir; skipping runtime copy (already in place)"
fi

# 3. Migrate device venv from legacy /data/opt install if needed.
#    Do not overwrite an existing venv at the SetupHelper path.
if [ ! -d "$INSTALL_DIR/.venv2" ] && [ -d "$LEGACY_OPT/.venv2" ]; then
    sep "migrating .venv2 from $LEGACY_OPT"
    cp -a "$LEGACY_OPT/.venv2" "$INSTALL_DIR/.venv2"
fi
if [ ! -d "$INSTALL_DIR/.venv" ] && [ -d "$LEGACY_OPT/.venv" ]; then
    sep "migrating .venv from $LEGACY_OPT"
    cp -a "$LEGACY_OPT/.venv" "$INSTALL_DIR/.venv"
fi

# 4. Refresh only shipped run scripts; preserve live supervise/ directories.
# Always use services/ (the immutable package source), not service/ (runtime).
mkdir -p "$INSTALL_DIR/service/$SVC_NAME/log"
for item in run log/run; do
    cp "$SRC_DIR/services/$SVC_NAME/$item" "$INSTALL_DIR/service/$SVC_NAME/$item.new"
    chmod +x "$INSTALL_DIR/service/$SVC_NAME/$item.new"
    mv "$INSTALL_DIR/service/$SVC_NAME/$item.new" "$INSTALL_DIR/service/$SVC_NAME/$item"
done
rm -f "$INSTALL_DIR/service/$SVC_NAME/down" "$INSTALL_DIR/service/$SVC_NAME/log/down"
mkdir -p "/var/log/$SVC_NAME"

# Exit old supervisors when migrating a symlink from a different runtime tree.
if [ -L "/service/$SVC_NAME" ] && \
    [ "$(readlink "/service/$SVC_NAME")" != "$INSTALL_DIR/service/$SVC_NAME" ]; then
    svc -dx "/service/$SVC_NAME" "/service/$SVC_NAME/log" 2>/dev/null || true
    sleep 2
fi

# A legacy real directory cannot be replaced by ln -sf (it nests the link).
if [ -d "/service/$SVC_NAME" ] && [ ! -L "/service/$SVC_NAME" ]; then
    svc -dx "/service/$SVC_NAME" "/service/$SVC_NAME/log" 2>/dev/null || true
    sleep 2
    rm -rf "/service/$SVC_NAME"
fi
ln -snf "$INSTALL_DIR/service/$SVC_NAME" "/service/$SVC_NAME"

# 6. Ensure boot persistence via /data/rc.local (NOT /data/rc/S99*).
#    /service is tmpfs; rewrite marker block on every update.
RC_LOCAL="/data/rc.local"
if [ ! -f "$RC_LOCAL" ]; then
    printf '#!/bin/sh\n' > "$RC_LOCAL"
    chmod +x "$RC_LOCAL"
fi
sed -i '/# === venus-os-observability service persistence ===/,/# === end venus-os-observability ===/d' "$RC_LOCAL" 2>/dev/null || true
HOOK=$(mktemp /data/.venus-observability-boot.XXXXXX)
cat > "$HOOK" << 'RCEOF'

# === venus-os-observability service persistence ===
# Recreate /service symlink on boot (lost since /service is tmpfs).
# NOTE: /data/rc/S99venus-os-observability.sh is NOT executed by Venus OS;
# boot hooks are only /data/rc.local and /data/rcS.local.
ln -snf /data/venus-os-observability/service/venus-os-observability /service/venus-os-observability
sleep 2
svc -u /service/venus-os-observability/log 2>/dev/null || true
svc -u /service/venus-os-observability 2>/dev/null || true
# === end venus-os-observability ===
RCEOF
awk -v hook="$HOOK" '
    function insert_hook() { while ((getline line < hook) > 0) print line; close(hook) }
    !inserted && /^[[:space:]]*exit[[:space:]]+0[[:space:]]*$/ { insert_hook(); inserted=1 }
    { print }
    END { if (!inserted) insert_hook() }
' "$RC_LOCAL" > "$RC_LOCAL.observability"
chmod +x "$RC_LOCAL.observability"
mv "$RC_LOCAL.observability" "$RC_LOCAL"
rm -f "$HOOK"
sep "refreshed rc.local boot persistence block"

# 7. Remove dead S99 hook (Venus never runs /data/rc/S99*).
if [ -e /data/rc/S99venus-os-observability.sh ]; then
    rm -f /data/rc/S99venus-os-observability.sh
    sep "removed dead /data/rc/S99venus-os-observability.sh"
fi

# 8. Give svscan a moment to spawn fresh supervisors.
sleep 3

# 10. Bring everything back up.
for svc in /service/$SVC_NAME/log /service/$SVC_NAME; do
    [ -e "$svc" ] && svc -u "$svc" 2>/dev/null || true
done

sep "installed version $(cat "$INSTALL_DIR/version" 2>/dev/null || echo unknown)"
sep "note: legacy tree $LEGACY_OPT left in place if present; active path is $INSTALL_DIR"
