"""erosgen - EROS system configurator (package).

The 1000-line monolith was split into cohesive modules (model / emit / mcu /
validate / cli). The public names below are re-exported so ``import erosgen``
keeps working for the test suite, fixtures/genmain/regen.py, and any other
caller - the tools/erosgen.py shim entrypoint imports cli.main from here.
"""

from .cli import main, write
from .diagnostics import Diagnostic, Diagnostics
from .emit import (driver_sources, emit_asw_skeleton, emit_config_c,
                   emit_config_h, emit_main_skeleton, emit_makefile,
                   emit_os_gen_h, emit_rte_c, emit_rte_cfg_h, emit_rte_h,
                   periph_defines)
from .errors import ConfigError, fail
from .model import Resource, System, Task, collect_diagnostics
from .models import ResolvedModel, resolve_model, resolve_models
from .parse import Calibration, ModelInterface, Signal, parse_model
from .report import report

__all__ = [
    "ConfigError", "fail",
    "Diagnostic", "Diagnostics", "collect_diagnostics",
    "parse_model", "ModelInterface", "Signal", "Calibration",
    "resolve_models", "resolve_model", "ResolvedModel",
    "System", "Task", "Resource",
    "emit_config_h", "emit_config_c", "emit_makefile", "emit_os_gen_h",
    "emit_asw_skeleton", "emit_main_skeleton", "driver_sources",
    "periph_defines", "emit_rte_cfg_h", "emit_rte_c", "emit_rte_h",
    "report", "main", "write",
]
