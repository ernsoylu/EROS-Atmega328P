"""Pure emitters: System -> generated file text. No filesystem side effects."""

from .config import emit_config_c, emit_config_h
from .makefile import driver_sources, emit_makefile, periph_defines
from .osgen import emit_os_gen_h
from .rte import emit_rte_c, emit_rte_cfg_h, emit_rte_h
from .skeletons import emit_asw_skeleton, emit_main_skeleton

__all__ = [
    "emit_config_h", "emit_config_c",
    "emit_makefile", "driver_sources", "periph_defines",
    "emit_os_gen_h",
    "emit_asw_skeleton", "emit_main_skeleton",
    "emit_rte_cfg_h", "emit_rte_c", "emit_rte_h",
]
