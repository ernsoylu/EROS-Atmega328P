"""Emitter for os_gen.h: board pin setup + alarm arming (always regenerated).

Kept in lockstep with the YAML so adding a task/pin propagates into the build
without editing hand-written main.c (the regeneration-drift fix).
"""

from ..backends import for_profile
from ..constants import GENERATED_BANNER, INCLUDE_EROS_H


def _driver_init_call(s, p):
    """The Init() call for peripheral p in Board_ConfigurePins: the profile
    default, or - for spi - built from peripherals.spi {mode, clock} so the SPI
    mode + clock divider are configurable without a driver change. Defaults keep
    the profile string (SPI_MODE0 / SPI_CLK_DIV16), so an spi with no config is
    byte-identical."""
    if p == "spi":
        cfg = s.peripherals.get("spi") or {}
        if cfg.get("mode") is not None or cfg.get("clock") is not None:
            mode = int(cfg.get("mode", 0))
            clock = int(cfg.get("clock", 16))
            return f"Spi_Init(SPI_MODE{mode}, SPI_CLK_DIV{clock});"
    return s.profile.driver_init[p]


def emit_os_gen_h(s):
    """Always-regenerated glue kept in lockstep with the YAML: board pin
    setup (from gpio + peripheral inits) and alarm arming. main.c calls
    these so adding a task/pin in the YAML propagates without editing
    hand-written code (the regeneration-drift fix)."""
    be = for_profile(s.profile)      # the code-gen backend (AVR register idioms)
    L = []
    L.append("/**")
    L.append(" * @file    os_gen.h")
    L.append(f" * @brief   {GENERATED_BANNER.format(src=s.src.name)}")
    L.append(" *")
    L.append(" * Regenerated on every erosgen run - never edit. Include it")
    L.append(" * from main.c and call Board_ConfigurePins() in StartupHook()")
    L.append(" * and OS_StartAlarms() in the autostart task, so pin and alarm")
    L.append(" * wiring stay in sync with the YAML.")
    L.append(" */")
    L.append("")
    L.append("#ifndef OS_GEN_H")
    L.append("#define OS_GEN_H")
    L.append("")
    L.append("#include <avr/io.h>")
    L.append(INCLUDE_EROS_H)
    for p in sorted(s.peripherals):
        L.append(f'#include "{s.profile.driver_header[p]}"')
    if s.models:
        L.append('#include "Rte.h"')
    L.append("")
    L.append("/** Pin directions/pull-ups + enabled-driver init. Call from")
    L.append(" *  StartupHook() (interrupts still disabled). */")
    L.append("static inline void Board_ConfigurePins(void)")
    L.append("{")
    # GPIO directions grouped by port for compactness.
    for g in s.gpio:
        port = g["pin"][1]      # B/C/D
        bit = g["pin"]          # e.g. PB5 - avr-libc defines the bit index
        label = g["name"] or g["pin"]
        if g["dir"] == "out":
            L.append(f"    {be.bit_set(f'DDR{port}', bit)};  /* {label} output */")
            if g["init"]:
                L.append(f"    {be.bit_set(f'PORT{port}', bit)};")
            else:
                L.append(f"    {be.bit_clear(f'PORT{port}', bit)};")
        else:
            L.append(f"    {be.bit_clear(f'DDR{port}', bit)};  /* {label} input */")
            if g["pullup"]:
                L.append(f"    {be.bit_set(f'PORT{port}', bit)};  /* pull-up */")
    for p in sorted(s.peripherals):
        if p in s.profile.driver_init:
            L.append(f"    {_driver_init_call(s, p)}")
    if s.models:
        L.append("    Rte_Init();  /* BSW init for bound ports + ASW init */")
    if (not s.gpio and not s.models
            and not any(p in s.profile.driver_init for p in s.peripherals)):
        L.append("    /* no gpio or auto-init drivers configured */")
    L.append("}")
    L.append("")
    L.append("/** Arm every cyclic alarm with ALIGNED releases (all first")
    L.append(" *  expiries at the base period so shared release points")
    L.append(" *  coincide). Call from the autostart task. */")
    L.append("static inline void OS_StartAlarms(void)")
    L.append("{")
    base = s.periodic[0].period_ticks
    for t in s.periodic:
        L.append(f"    (void)SetRelAlarm(ALARM_{t.name}, {base}u, "
                 f"TASK_{t.name}_PERIOD_TICKS);")
    L.append("}")
    L.append("")
    L.append("#endif /* OS_GEN_H */")
    return "\n".join(L) + "\n"
