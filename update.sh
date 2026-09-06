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
RUNTIME_ITEMS="src version setup gitHubInfo pyproject.toml setup.py config.example.yaml"

sep() { echo "=== venus-os-observability update: $*"; }

# 1. Stop the services BEFORE touching files.
for svc in /service/$SVC_NAME/log /service/$SVC_NAME; do
    [ -e "$svc" ] && svc -dk "$svc" 2>/dev/null || true
done
sleep 1

# 1c. Reap stale daemontools supervise processes left behind by earlier
#     updates (inode churn under $INSTALL_DIR/service). Also reap anything
#     still running from the legacy /data/opt path during migration.
#     rm -rf (not rm -f): a real directory at /service/$SVC_NAME would make
#     ln -sf nest the link inside it.
rm -rf "/service/$SVC_NAME"
sleep 2
for pid in /proc/[0-9]*; do
    cwd=$(readlink "$pid/cwd" 2>/dev/null) || continue
    case "$cwd" in
        "$INSTALL_DIR/service/"*|"$INSTALL_DIR"|"$LEGACY_OPT/service/"*|"$LEGACY_OPT")
            kill -9 "${pid##*/}" 2>/dev/null || true
            ;;
        *)
            ;;
    esac
done
sleep 1

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

# 4. Install daemontools services: every dir under service/ and services/
#    maps to INSTALL_DIR/service/. Drop leftover `down` files so a deploy
#    always means "run the new version".
mkdir -p "$INSTALL_DIR/service"
for svc in "$SRC_DIR/service"/* "$SRC_DIR/services"/*; do
    [ -d "$svc" ] || continue
    name="$(basename "$svc")"
    rm -rf "$INSTALL_DIR/service/$name"
    cp -a "$svc" "$INSTALL_DIR/service/$name"
    find "$INSTALL_DIR/service/$name" -type f -name run -exec chmod +x {} \; 2>/dev/null || true
    find "$INSTALL_DIR/service/$name" -name down -exec rm -f {} \; 2>/dev/null || true
done

# 5. Refresh /service symlink (nested layout matching dbus-ev).
ln -sf "$INSTALL_DIR/service/$SVC_NAME" /service/

# 6. Ensure boot persistence via /data/rc.local (NOT /data/rc/S99*).
#    /service is tmpfs; rewrite marker block on every update.
RC_LOCAL="/data/rc.local"
if [ ! -f "$RC_LOCAL" ]; then
    printf '#!/bin/sh\n' > "$RC_LOCAL"
    chmod +x "$RC_LOCAL"
fi
sed -i '/# === venus-os-observability service persistence ===/,/# === end venus-os-observability ===/d' "$RC_LOCAL" 2>/dev/null || true
cat >> "$RC_LOCAL" << 'RCEOF'

# === venus-os-observability service persistence ===
# Recreate /service symlink on boot (lost since /service is tmpfs).
# NOTE: /data/rc/S99venus-os-observability.sh is NOT executed by Venus OS;
# boot hooks are only /data/rc.local and /data/rcS.local.
rm -rf /service/venus-os-observability
ln -sf /data/venus-os-observability/service/venus-os-observability /service/venus-os-observability
sleep 2
svc -u /service/venus-os-observability/log 2>/dev/null || true
svc -u /service/venus-os-observability 2>/dev/null || true
# === end venus-os-observability ===
RCEOF
sep "refreshed rc.local boot persistence block"

# 7. Remove dead S99 hook (Venus never runs /data/rc/S99*).
if [ -e /data/rc/S99venus-os-observability.sh ]; then
    rm -f /data/rc/S99venus-os-observability.sh
    sep "removed dead /data/rc/S99venus-os-observability.sh"
fi

# 8. Give svscan a moment to spawn fresh supervisors.
sleep 3

# 9. Let PackageManager rediscover the package (version changed).
svc -t /service/PackageManager 2>/dev/null || true

# 10. Bring everything back up.
for svc in /service/$SVC_NAME/log /service/$SVC_NAME; do
    [ -e "$svc" ] && svc -u "$svc" 2>/dev/null || true
done

sep "installed version $(cat "$INSTALL_DIR/version" 2>/dev/null || echo unknown)"
sep "note: legacy tree $LEGACY_OPT left in place if present; active path is $INSTALL_DIR"
