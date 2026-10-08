# Python dependency locks

`uv.lock` selects the project, build and security-tool dependency versions for
Python 3.12. The `build` dependency group includes the package frontend and
backend; `security` includes Bandit. CI and container installs verify the hashes
in the exported requirements files before installing third-party artifacts.

`bash scripts/ci.sh --install` creates the local check environment from
`.github/requirements-ci.txt`, then installs this checkout without dependency
resolution or build isolation. Package release builds also disable isolation
and use the installed, locked build backend. Local source is built from the
reviewed checkout; it is not a downloaded dependency with a registry hash.

The Docker builder uses a separate build environment, installs runtime wheels
from `.github/requirements-runtime.txt`, then installs the locally built project
wheel offline with dependency resolution disabled. The final image retains the
runtime environment and Ubuntu's matching Python/D-Bus/GI packages. Container
base digests remain pinned; Ubuntu package installation still follows its
security-update repositories.

To update dependencies with uv 0.12.7, review the `uv.lock` diff and regenerate
all four exports together:

```sh
uv lock
uv export --locked --no-default-groups --no-emit-project --output-file .github/requirements-runtime.txt
uv export --locked --extra dev --group build --group security --no-emit-project --output-file .github/requirements-ci.txt
uv export --locked --only-group build --no-emit-project --output-file .github/requirements-release-build.txt
uv export --locked --only-group security --no-emit-project --output-file .github/requirements-release-security.txt
bash scripts/ci.sh --install
bash scripts/ci.sh
bash scripts/ci.sh security
```

Commit the manifest, lock and exports together. Exports retain platform markers
and the hashes of published artifacts; `--only-binary=:all:` in the install
commands rejects unsupported wheel platforms instead of compiling with an
unreviewed backend. Validate the actual Docker image on Linux before merging
container changes, including the existing native-library import check.

The alert MQTT bridge retains its independent Python 3.14 / Paho MQTT 1.6.1
compatibility contract. Its `requirements.txt` verifies the original Paho source
archive; `requirements-build.txt` pins the wheel-building backend. The builder
creates the wheel without isolation, and the final image installs that wheel
offline. Regenerate its build lock separately:

```sh
uv pip compile --universal --python-version 3.14 --generate-hashes --only-binary :all: alert-mqtt-bridge/requirements-build.in -o alert-mqtt-bridge/requirements-build.txt
```
