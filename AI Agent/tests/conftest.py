import sys
from pathlib import Path

# The project uses flat modules; make them importable from tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
