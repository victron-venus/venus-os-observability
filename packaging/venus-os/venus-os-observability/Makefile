# The former OpenWrt-style recipe and systemd hooks do not target Venus OS.
# Use SetupHelper/update.sh. Native IPKs require a tested BitBake recipe for
# the matching Victron Venus OS release and machine.
.PHONY: all
all:
	@echo "Use SetupHelper or sh update.sh on Venus OS; no validated IPK recipe is provided." >&2
	@exit 1
