# Release-candidate fuzz testing

## Scope and assertions

**correlation.** The real correlation-context and header propagation functions receive synthetic identifiers, including Unicode and punctuation. Header extraction must preserve explicitly supplied IDs and context tokens must restore prior state. No trace exporter, D-Bus subscription or network connection starts.

The harness is [fuzz/fuzz_correlation.py](../fuzz/fuzz_correlation.py), with versioned
synthetic inputs in [fuzz/corpus/correlation](../fuzz/corpus/correlation). It exercises project code rather than
only a standard-library JSON decoder. Expected validation rejections are handled
at their documented boundary; assertion failures and unexpected exceptions fail
the run. Seeds contain no credentials, device identities or captured user data.

## When it runs and what it costs

The canonical release pipeline calls the reusable fuzz workflow in
`victron-venus/venus-os-ci-toolkit`, pinned by commit in `.release-policy.json`.
`.github/fuzz.json` declares this repository's setup commands, target and seed
corpus. Only a **public repository release candidate (RC)** starts the campaign.
Normal pull requests, branch pushes, beta releases and schedules do not run it.
Private repositories are rejected by the shared eligibility check.

This target has a 60-second mutation budget, a 10-second per-input timeout and a
2048 MiB libFuzzer memory limit. Setup, seed replay and artifact upload add some
wall time; 60 seconds is not a promise of total Actions runtime. Shared limits cap
the number of targets and combined budget. Ordinary unit tests still run on their
existing cadence. Regression tests for fixed findings remain fast unit tests.

An RC must pass the fuzz gate before release publication. The receipt records
source revision, manifest hash and result. Committed corpus files are bound
transitively by that source revision; there is no separate seed digest or complete
toolchain receipt. Stable promotion
reuses the accepted RC result for the same source/artifacts; it is not a second
campaign. A changed source or failed gate requires a corrected RC and new evidence.
Do not bypass a failed gate or treat a skipped job as a passing campaign.

## Isolation and reproducibility

CI installs the hash-pinned Python 3.12/Linux x86-64 runtime from
`.github/requirements-fuzz.txt` in `.fuzz-venv`. Setup uses explicit argument arrays;
the shared runner executes the target in a network-isolated environment. No device
credentials, production broker, Cerbo, Home Assistant or live actuator is needed.
The harness uses fresh state for each input (or immutable fixtures) and bounded
inputs. Fuzz results are evidence about these targets, not a certification of
hardware safety or exhaustive coverage of every component.

On a disposable Linux x86-64 development environment with Python 3.12:

```sh
python3.12 -m venv .fuzz-venv
.fuzz-venv/bin/python -m pip install --require-hashes --only-binary=:all: -r .github/requirements-fuzz.txt
.fuzz-venv/bin/python fuzz/fuzz_correlation.py -max_len=4096 fuzz/corpus/correlation -runs=0
.fuzz-venv/bin/python fuzz/fuzz_correlation.py -max_len=4096 fuzz/corpus/correlation -max_total_time=60 -timeout=10 -rss_limit_mb=2048
```

The first target command replays the committed corpus; the second runs mutations.
Use a copied corpus for local campaigns if you do not want libFuzzer to add seeds
to the working tree. The pinned Atheris wheel is Linux x86-64 specific. A local
unit-test or seed-only replay on another platform is not an instrumented fuzz run.

## Handling a failure

1. Download the failing run's diagnostics and minimized/crashing input from its
   Actions artifacts. Record the exact source SHA, manifest and tool versions.
2. Replay only that input with the same target/runtime, for example
   `.fuzz-venv/bin/python fuzz/fuzz_correlation.py -max_len=4096 path/to/failing-input`.
3. Determine whether the failed assertion expresses the intended public contract.
   Fix project code or an incorrect oracle with review; do not broadly catch
   exceptions, drop inputs or weaken the gate to hide a failure.
4. Add a small deterministic regression test and a synthetic corpus seed, run the
   relevant unit tests, and publish a new RC for another full bounded campaign.
5. Report a security-sensitive finding through [SECURITY.md](../SECURITY.md),
   avoiding sensitive data in public artifacts or issues.

The README badge **Fuzzing: RC only** describes this configured policy. It is a
static label, not a live success indicator or claim of Google OSS-Fuzz enrollment.
The specific RC's Actions result and receipt are the execution evidence.
