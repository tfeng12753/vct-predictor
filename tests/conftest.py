import gzip
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_html():
    return lambda name: gzip.open(FIX / f"{name}.html.gz", "rt").read()
