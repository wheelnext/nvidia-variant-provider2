from pathlib import Path

import pytest

from variant_provider.fixtures import inventory, run_case

ROOT = Path(__file__).resolve().parents[2]
CASES = inventory(ROOT / "json/tests/cases")
assert CASES, "Shared fixture discovery found no cases"


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_shared_behavior(path):
    run_case(path)
