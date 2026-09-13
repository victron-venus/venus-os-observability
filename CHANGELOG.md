# Changelog

## 0.1.5 - 2026-09-12

- Resolve D-Bus senders to stable Venus service names using asynchronous startup
  discovery and `NameOwnerChanged`; remove superseded owners instead of retaining
  transient process names in metric labels and the owner cache.
- Preserve startup signal batches until their service is resolved, with a bounded
  queue, expiry counter and rate-limited diagnostic. Retry failed discovery without
  blocking the GLib loop or scanning all owners for every measurement.
- Mark previously published Prometheus and OpenTelemetry gauges unavailable when
  their service loses or replaces its owner; keep stable counters cumulative and
  recover on fresh values, including valid zero readings.
- Register the message filter once across subscriptions and cancel owner lookups,
  pending batches and timer callbacks on shutdown. Include the new owner tracker
  in the validated native SetupHelper archive.

The alert relay, deployment configuration and installer behavior are unchanged.

## 0.1.4 - 2026-09-12

- Avoid recording D-Bus trace payloads when tracing is disabled and normalize
  D-Bus string attributes when tracing is enabled.
- Export invalid numeric D-Bus values as unavailable without dropping valid
  measurements in the same signal; preserve real zero readings.
- Load public helpers lazily and shut down telemetry only once.
- Preserve service and logger supervisor directories during repeat installs,
  check runtime dependencies before stopping the service, and place the boot
  hook before `exit 0` in `/data/rc.local`.
- Publish a validated SetupHelper source archive with SHA-256 checksums instead
  of invoking the unsupported historical IPK recipe.
- Publish wheel and source distributions on GitHub even when optional PyPI
  credentials are absent; report a skipped PyPI publication explicitly.

The source archive uses existing firmware D-Bus/GLib bindings and an existing
compatible Python environment. It does not bundle dependencies or provide a
universal ARM wheel set. Prepare matching offline wheels before a first install
or a Python ABI change. Container releases remain companion-host artifacts.
