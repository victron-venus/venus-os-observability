import sys
import types
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("venus_observability")
package.__path__ = [str(ROOT / "src/venus_observability")]
sys.modules[package.__name__] = package

with atheris.instrument_imports():
    from venus_observability.correlation import (
        extract_correlation_id_from_headers,
        get_correlation_id,
        inject_correlation_id_into_headers,
        reset_correlation_id,
        set_correlation_id,
    )


def test_one_input(data: bytes) -> None:
    value = data.decode("utf-8", errors="replace")
    if not value:
        return
    before = get_correlation_id()
    token = set_correlation_id(value)
    try:
        assert get_correlation_id() == value  # nosec B101 - intentional test oracle
        headers = inject_correlation_id_into_headers({}, value)
        assert extract_correlation_id_from_headers(headers) == value  # nosec B101 - intentional test oracle
        upper = {key.upper(): item for key, item in headers.items()}
        assert extract_correlation_id_from_headers(upper) == value  # nosec B101 - intentional test oracle
    finally:
        reset_correlation_id(token)
    assert get_correlation_id() == before  # nosec B101 - intentional test oracle


if __name__ == "__main__":
    atheris.Setup(sys.argv, atheris.instrument_func(test_one_input))
    atheris.Fuzz()
