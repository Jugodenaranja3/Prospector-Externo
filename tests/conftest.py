import sys
from pathlib import Path

# Añadir src y raíz al PYTHONPATH para tests
project_root = Path(__file__).resolve().parents[1]
src_dir = project_root / "src"

if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
