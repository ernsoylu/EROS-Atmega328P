"""erosgen - EROS system configurator (the "OIL file" compiler).

Reads one app.yaml per application and generates the static OS
configuration (config.h / config.c), the application Makefile, and -
for files that do not exist yet - per-rate ASW skeletons and a main.c
integration skeleton. Only peripherals listed in the YAML are compiled
in; buffer geometry (UART rings, pool arena) is set from the YAML so
RAM is spent deliberately.

Usage:
    python3 tools/erosgen.py <app.yaml> [--check]

    --check   validate + report only, write nothing

Design rules encoded here (see README.md / codegen/README.md):
  * TaskID == static priority == ready-mask bit; max 8 tasks.
  * Priorities are assigned rate-monotonically: fastest period =
    highest priority; aperiodic (activated/chained) tasks sit below
    the periodic set; the autostart init task is lowest.
  * One cyclic alarm per periodic task, IDs ordered fastest first.
  * Resource ceiling = highest-priority user (computed, not typed).
  * Schedulability gate: sum of all periodic WCETs must fit in the
    base (fastest) period - blocking, never corruption, but enforced.
"""

import sys
from pathlib import Path

from .constants import MAIN_C
from .diagnostics import Diagnostics
from .emit import (emit_asw_skeleton, emit_config_c, emit_config_h,
                   emit_main_skeleton, emit_makefile, emit_os_gen_h,
                   emit_rte_c, emit_rte_cfg_h, emit_rte_h)
from .errors import ConfigError
from .model import System
from .models import resolve_models
from .report import report

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("erosgen: PyYAML required (pip install pyyaml)")


def write(path, content, check_only, overwrite=True):
    if not overwrite and path.exists():
        return "kept"
    if check_only:
        return "would write"
    path.write_text(content)
    return "wrote"


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    check_only = "--check" in argv
    if len(args) != 1:
        print(__doc__)
        return 2
    src = Path(args[0]).resolve()
    if not src.exists():
        print(f"erosgen: {src} not found")
        return 2
    app_dir = src.parent

    try:
        doc = yaml.safe_load(src.read_text())
        s = System(doc, src)
        outputs = [
            (app_dir / "config.h", emit_config_h(s), True),
            (app_dir / "config.c", emit_config_c(s), True),
            (app_dir / "Makefile", emit_makefile(s, app_dir), True),
            (app_dir / MAIN_C, emit_main_skeleton(s), False),
        ]
        # os_gen.h (pin + alarm glue) is refreshed only for apps that use
        # it: a freshly generated main.c includes it, and any main.c that
        # references it keeps getting refreshed. Hand-written mains that
        # manage their own startup (the reference demo) are left alone.
        main_path = app_dir / MAIN_C
        uses_os_gen = (not main_path.exists()) or \
            ("os_gen.h" in main_path.read_text())
        if uses_os_gen:
            outputs.append((app_dir / "os_gen.h", emit_os_gen_h(s), True))
        for t in s.periodic:
            if t.name in s.model_task_names:
                continue  # model task body is Task_<model> in the generated Rte.c
            fname = app_dir / f"asw_{t.period_ms}ms.c"
            outputs.append((fname, emit_asw_skeleton(s, t), False))
        # RTE generation: a models: section emits Rte.h/Rte_Cfg.h/Rte.c; the
        # model is already wired as an OS task/alarm in config.* by System.
        if s.models:
            rsink = Diagnostics(strict=True)  # binding errors are fatal, like config
            for rm in resolve_models(doc, app_dir, rsink):
                outputs.append((app_dir / "Rte.h",
                                emit_rte_h(rm, src.name), True))
                outputs.append((app_dir / "Rte_Cfg.h",
                                emit_rte_cfg_h(rm, src.name), True))
                outputs.append((app_dir / "Rte.c",
                                emit_rte_c(rm, src.name, integrated=True), True))
    except ConfigError as e:
        print(e)
        return 1

    report(s)
    for path, content, overwrite in outputs:
        action = write(path, content, check_only, overwrite)
        print(f"  {action}: {path.relative_to(app_dir)}")
    missing = [f for f in s.sources if not (app_dir / f).exists()]
    for f in missing:
        print(f"  WARNING: listed source {f} does not exist (yet)")
    return 0
