"""Enforcement guardrail: views must build themed controls via the
app.gui.components design system, not hand-rolled ft.* primitives.

This is the mechanical half of the design-system contract (the judgment half is
the ui-design-system-review skill). It scans the view layer source for raw
constructions of controls that HAVE a library factory and fails on any not in
the documented allowlist. New UI that bypasses the library trips this test.
"""
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

GUI = PROJECT_ROOT / "app" / "gui"

# Files that make up the view layer (where UI is assembled). The component
# package itself and glass.py are the library/foundation and are exempt.
VIEW_FILES = sorted(GUI.glob("views/*.py")) + [GUI / "log_panel.py"]

# Raw themed controls that have a c.* factory and must NOT be built directly.
FORBIDDEN = re.compile(
    r"\bft\.(TextField|Switch|Slider|Dropdown|FilledButton|OutlinedButton)\("
)

# Documented exceptions: genuinely custom controls with no equivalent factory.
# Format: "<filename>:<ft.Control>" -> reason. Keep this list SHORT and justified;
# prefer extending the library over adding an entry.
ALLOWLIST = {
    # (filename, control): reason
}


def test_views_use_component_library_not_raw_controls():
    violations = []
    for f in VIEW_FILES:
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in FORBIDDEN.finditer(line):
                ctrl = m.group(1)
                key = f"{f.name}:ft.{ctrl}"
                if key in ALLOWLIST:
                    continue
                violations.append(f"{f.name}:{i}: ft.{ctrl}( -> use app.gui.components")
    assert not violations, (
        "Raw themed controls in the view layer (build them via app.gui.components, "
        "or add a justified ALLOWLIST entry):\n" + "\n".join(violations)
    )
