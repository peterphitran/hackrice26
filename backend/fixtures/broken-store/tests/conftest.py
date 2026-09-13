import sys
from pathlib import Path

fixture_root = Path(__file__).parents[1]
source_root = fixture_root / "template" if (fixture_root / "template").is_dir() else fixture_root
sys.path.insert(0, str(source_root))
