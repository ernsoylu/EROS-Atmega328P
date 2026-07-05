"""EROS Configurator GUI - a thin PySide6 view over the erosgen engine.

All domain logic lives in the engine (tools/erosgen): validation, diagnostics,
RTE resolution, code generation. This package only loads a project, renders the
engine's results, and drives generate/build. Keep it that way - no validation or
codegen logic here.

Run: uv run --extra gui python -m gui [path/to/app.yaml]
"""
import sys
from pathlib import Path

# erosgen lives under tools/ and is run in place (pyproject package=false), so
# put it on the path for `import erosgen`.
_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
