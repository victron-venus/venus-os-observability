# Documentation index

These documents describe deploying and operating the OpenTelemetry/Prometheus
observability stack for Venus OS, its alert relay and the evidence behind it.

## Get started

| Document | Description |
| --- | --- |
| [Deployment evidence and acceptance](deployment-evidence.md) | The verification record and acceptance checklist for a deployment. |
| [Python dependency locks](dependency-locks.md) | Python 3.12 and the locked dependency groups. |
| [Quality, builds and evidence](quality-and-build.md) | Reproducing builds and checks with the pinned toolchain. |

## Configure

| Document | Description |
| --- | --- |
| [OTLP transport and migration](otlp-transport.md) | Optional tracing, the `tempo` extra and offline installs. |
| [Alert relay TLS policy](alert-relay-tls.md) | Exact key sizes for SMTP STARTTLS and Telegram HTTPS, with proxy notes. |

## Operate and release

| Document | Description |
| --- | --- |
| [Release operations](release-packaging.md) | Validation workflows run by beta and RC candidates before building. |
| [Release version inputs](release-versioning.md) | The `versioning` fields that identify a release. |
| [CI orchestration](CI-ORCHESTRATION.md) | How the quality gate and release pipeline divide validation. |
| [Release-candidate fuzz testing](fuzzing.md) | The bounded RC fuzz targets for correlation context and header propagation. |

## Assurance and evidence

| Document | Description |
| --- | --- |
| [Security assurance case](security-assurance.md) | A bounded argument for the reviewed implementation, not a certificate. |
| [OpenSSF Best Practices evidence](openssf-evidence.md) | Evidence index for the Passing and Silver self-assessment. |
| [Dashboard screenshot artwork](images/README.md) | Source of the Grafana capture and how the image is regenerated. |

Shared repository policy: [release-workflow.md](release-workflow.md), [runner-preflight.md](runner-preflight.md), [release-read-safety.md](release-read-safety.md), [VERSIONING.md](VERSIONING.md), [rc-fuzzing.md](rc-fuzzing.md) are generated or shared boilerplate maintained in [victron-venus/venus-os-ci-toolkit](https://github.com/victron-venus/venus-os-ci-toolkit).
