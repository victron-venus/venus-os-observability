#!/usr/bin/env python3
"""Setup script for venus-os-observability package."""

from setuptools import find_packages, setup

setup(
    name="venus-os-observability",
    version="0.1.0",
    description="OpenTelemetry/Prometheus observability for Venus OS",
    author="Victron Venus Team",
    license="MIT",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    package_data={
        "venus_observability": ["systemd/*.service", "config.example.yaml"],
    },
    entry_points={
        "console_scripts": [
            "venus-os-observability = venus_observability.__main__:main",
        ],
    },
    install_requires=[
        "opentelemetry-api>=1.21,<1.30",
        "opentelemetry-sdk>=1.21,<1.30",
        "opentelemetry-exporter-prometheus>=0.65",
        "opentelemetry-exporter-otlp>=1.21,<1.30",
        "opentelemetry-exporter-otlp-proto-grpc>=1.21,<1.30",
        "opentelemetry-instrumentation>=0.17",
        "prometheus-client>=0.19",
        "paho-mqtt>=2.0",
        "dbus-next>=0.2",
        "pyyaml>=6.0",
        "structlog>=24.0",
        "click>=8.1",
    ],
    python_requires=">=3.11",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.11",
        "Topic :: System :: Monitoring",
    ],
)
