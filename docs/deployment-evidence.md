# Deployment evidence and acceptance

Reviewed 12 October 2026. Source tests, a release archive, a running process and
successful notification delivery establish different things. Keep the exact
installed artifact and configuration associated with each verification record.

## Existing public evidence

The [12 September Venus service audit](https://github.com/victron-venus/.github/blob/5d470bab19a7e37b1f9873e5aa55bd81c3afba52/docs/VENUS-SERVICE-AUDIT-2026-09-12.md)
records a populated Prometheus endpoint returning HTTP 200 after targeted
exporter repairs. The target was a Raspberry Pi 3 Model B on Venus OS v3.75,
not a physical Cerbo GX. The [closure record](https://github.com/victron-venus/.github/blob/5d470bab19a7e37b1f9873e5aa55bd81c3afba52/docs/VENUS-AUDIT-CLOSURE-2026-09-12.md)
records the 0.1.5 exporter release, native/runtime file checks and a separately
inspected alert bridge using 0.1.4 source. It explicitly bounds the observations
and outstanding device risks.

The README's earlier email and Telegram setup notes report delivery tests from
24 August 2026. They are historical operator observations. They do not establish
today's recipient configuration, every alert rule, delivery reliability over time
or deployment of the latest release. The source audit and a live metrics response
also do not prove end-to-end trace correlation.

## Runtime boundaries

The native service subscribes to Venus D-Bus `ItemsChanged` signals and exports
metrics. It does not start an MQTT subscriber or install instrumentation in
`inverter-control`. The correlation helpers require explicit adoption by each
participating application; native D-Bus messages do not automatically carry that
context. Tracing also requires a configured OTLP endpoint. A correlation helper's
unit test is not a distributed trace captured from operating equipment.

The alert relay is a separate application. Deploying the exporter does not
configure Grafana rules, notification policies, SMTP or Telegram credentials.
The generic [MQTT observability project](https://github.com/4alvit/mqtt-observability-opentelemetry)
is another deployment with its own observer and broker-statistics exporter.

## Check an installation

1. Record the release asset checksum or running image digest and compare the
   installed runtime files with that artifact. Preserve the current configuration
   and rollback artifact. A Git tag or process name alone is insufficient.
2. Read supervisor state and recent logs. Fetch `/metrics` and verify the expected
   service labels and values against current source observations. HTTP 200 with
   no samples, old readings or only process metrics does not pass this check.
3. Verify that Prometheus scrapes the intended exporter and that query timestamps
   advance. Check source invalidation/recovery in a controlled fixture first;
   coordinate any real device outage test separately.
4. If tracing is enabled, observe a new span in the configured backend. Claim
   cross-service correlation only after recording the same trace through the
   explicitly instrumented participants.
5. Send a Grafana test notification through the installed contact point and
   verify receipt in every enabled channel. Separately test rule evaluation,
   firing and resolved delivery during an agreed maintenance window. Restore
   the monitored service even if an alert or recipient check fails.
6. Record the observation interval, software/firmware versions, successful,
   failed and skipped checks. Keep detailed telemetry and credentials private;
   publish only a sanitized summary with the artifact identity and scope.

Repeat the relevant checks after a firmware/ABI change, exporter or relay
upgrade, notification-policy edit or credential rotation. An old successful
record remains historical evidence; it is not the current health status.
