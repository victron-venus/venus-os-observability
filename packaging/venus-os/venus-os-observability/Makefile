# Venus OS .ipk package build for venus-os-observability
# Run inside Venus OS SDK container: docker run -v $PWD:/src victron/venus-sdk:latest make -C /src/packaging/venus-os/venus-os-observability

PKG_NAME := venus-os-observability
PKG_VERSION := 0.1.0
PKG_RELEASE := 1
PKG_SOURCE := $(PKG_NAME)-$(PKG_VERSION).tar.gz
PKG_SOURCE_URL := https://github.com/victron-venus/venus-os-observability/archive/refs/tags/v$(PKG_VERSION).tar.gz
PKG_HASH := $(shell sha256sum $(PKG_SOURCE) 2>/dev/null | cut -d' ' -f1)

PKG_MAINTAINER := Victron Venus Team <info@victronenergy.com>
PKG_LICENSE := MIT
PKG_LICENSE_FILES := LICENSE

PKG_BUILD_DIR := $(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)

# Python package dependencies (installed via opkg)
DEPENDS := +python3 +python3-opentelemetry +python3-prometheus-client +python3-paho-mqtt +python3-dbus-next +python3-pyyaml +python3-structlog +python3-click

include $(INCLUDE_DIR)/package.mk
include $(INCLUDE_DIR)/python3-package.mk

define Package/$(PKG_NAME)
  SECTION := utils
  CATEGORY := Victron
  TITLE := Venus OS Observability - OpenTelemetry/Prometheus for Victron
  URL := https://github.com/victron-venus/venus-os-observability
  DEPENDS := $(DEPENDS)
endef

define Package/$(PKG_NAME)/description
  OpenTelemetry/Prometheus observability for Venus OS:
  - D-Bus signal listener with OTel tracing
  - Prometheus metrics exporter for Victron inverter state
  - Systemd service for Cerbo GX
endef

define Build/Prepare
	$(call Build/Prepare/Default)
	# Copy source files
	$(CP) -r $(PKG_BUILD_DIR)/src/* $(PKG_BUILD_DIR)/
endef

define Build/Compile
	# Build Python wheel from source
	cd $(PKG_BUILD_DIR); python3 setup.py bdist_wheel
endef

define Package/$(PKG_NAME)/install
	$(INSTALL_DIR) $(1)/opt/venus-os-observability
	$(INSTALL_DIR) $(1)/etc/venus-os-observability
	$(INSTALL_DIR) $(1)/etc/systemd/system
	$(INSTALL_DIR) $(1)/usr/lib/python3/dist-packages

	# Install Python package
	$(CP) $(PKG_BUILD_DIR)/dist/*.whl $(1)/usr/lib/python3/dist-packages/

	# Install config
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config.example.yaml $(1)/etc/venus-os-observability/config.yaml

	# Install systemd service
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/systemd/venus-os-observability.service $(1)/etc/systemd/system/
endef

define Package/$(PKG_NAME)/postinst
#!/bin/sh
systemctl daemon-reload
systemctl enable venus-os-observability
systemctl start venus-os-observability
endef

define Package/$(PKG_NAME)/prerm
#!/bin/sh
systemctl stop venus-os-observability
systemctl disable venus-os-observability
endef

$(eval $(call Python3Package,$(PKG_NAME)))
$(eval $(call BuildPackage,$(PKG_NAME)))
