"""Emitters for the RTE: Rte_Cfg.h (declarative binding) and Rte.c (adapters).

Generated from a ResolvedModel (models.py). Mirrors the hand-written rte/
template (see rte/README.md) so the output compiles against the same drivers:
ADC_Read/ADC_Init for adc ports, DDRx/PORTx/PINx for dio ports. Ports with a
driver this emitter doesn't handle yet produce a visible #error.
"""

from ..constants import GENERATED_BANNER

_DRIVER_HEADER = {"adc": "adc.h", "pwm": "pwm.h"}  # dio uses raw avr/io.h

_DEF_PAD = 26  # macro-name column so every #define value aligns


def _def(name, value):
    return f"#define {name:<{_DEF_PAD}} {value}"


def _cfg_defines(port):
    """Rte_Cfg.h #define lines for one bound port (no trailing blank)."""
    tag, p = port.tag, port.params
    L = [_def(f"RTE_CFG_{tag}_SIGNAL", port.signal.name)]
    if port.driver == "adc":
        L.append(_def(f"RTE_CFG_{tag}_ADC_CH", f"{int(p['channel'])}u"))
    elif port.driver == "dio":
        prt, bit = str(p["port"]).upper(), int(p["bit"])
        L.append(_def(f"RTE_CFG_{tag}_DDR", f"DDR{prt}"))
        if port.direction == "out":
            L.append(_def(f"RTE_CFG_{tag}_PORT", f"PORT{prt}"))
        else:
            L.append(_def(f"RTE_CFG_{tag}_PIN", f"PIN{prt}"))
        L.append(_def(f"RTE_CFG_{tag}_BIT", f"{bit}u"))
    return L


def emit_rte_cfg_h(rm, src_name):
    L = []
    L.append("/**")
    L.append(" * @file    Rte_Cfg.h")
    L.append(f" * @brief   {GENERATED_BANNER.format(src=src_name)}")
    L.append(" *")
    L.append(f" * Declarative ASW<->BSW binding for the '{rm.name}' SWC: which")
    L.append(" * driver feeds each input port, which each output port actuates,")
    L.append(" * and the runnable rate. Pure configuration, no logic.")
    L.append(" */")
    L.append("")
    L.append("#ifndef RTE_CFG_H")
    L.append("#define RTE_CFG_H")
    L.append("")
    L.append("/* ---- SWC identity (ASW entry points) ---------------------------- */")
    L.append(_def("RTE_CFG_INIT_FN", rm.init_fn))
    L.append(_def("RTE_CFG_RUNNABLE_FN", rm.runnable_fn))
    L.append("")
    if rm.inputs:
        L.append("/* ---- Input ports: BSW sensor -> ASW port ------------------------ */")
        for port in rm.inputs:
            L.append(f"/* {port.signal.name} ({port.signal.ctype}) <- "
                     f"{port.driver} */")
            L.extend(_cfg_defines(port))
        L.append("")
    if rm.outputs:
        L.append("/* ---- Output ports: ASW port -> BSW actuator --------------------- */")
        for port in rm.outputs:
            L.append(f"/* {port.signal.name} ({port.signal.ctype}) -> "
                     f"{port.driver} */")
            L.extend(_cfg_defines(port))
        L.append("")
    L.append("/* ---- Scheduling: runnable rate assigned to the OS -------------- */")
    L.append(_def("RTE_CFG_PERIOD_MS", f"{int(rm.rate_ms)}u"))
    L.append("")
    L.append("#endif /* RTE_CFG_H */")
    return "\n".join(L) + "\n"


def _adapter(port):
    """The Rte_Read_*/Rte_Write_* adapter definition for one bound port."""
    tag, stem = port.tag, port.stem
    if port.driver == "adc":
        return [f"static uint16_t Rte_Read_{stem}(void)",
                "{",
                f"    return ADC_Read(RTE_CFG_{tag}_ADC_CH);",
                "}"]
    if port.driver == "dio" and port.direction == "in":
        return [f"static uint8_t Rte_Read_{stem}(void)",
                "{",
                f"    return (uint8_t)((RTE_CFG_{tag}_PIN >> RTE_CFG_{tag}_BIT) & 1u);",
                "}"]
    if port.driver == "dio" and port.direction == "out":
        return [f"static void Rte_Write_{stem}(uint8_t on)",
                "{",
                "    if (on)",
                "    {",
                f"        RTE_CFG_{tag}_PORT |= (uint8_t)(1u << RTE_CFG_{tag}_BIT);",
                "    }",
                "    else",
                "    {",
                f"        RTE_CFG_{tag}_PORT &= (uint8_t)~(1u << RTE_CFG_{tag}_BIT);",
                "    }",
                "}"]
    return [f'#error "RTE emit: driver \'{port.driver}\' '
            f'({port.direction}) not supported yet"']


def emit_rte_h(rm, src_name):
    L = []
    L.append("/**")
    L.append(" * @file    Rte.h")
    L.append(f" * @brief   {GENERATED_BANNER.format(src=src_name)}")
    L.append(" *")
    L.append(f" * RTE public API for the '{rm.name}' model<->OS integration.")
    L.append(" */")
    L.append("")
    L.append("#ifndef RTE_H")
    L.append("#define RTE_H")
    L.append("")
    L.append("/** BSW init for the bound ports + ASW init. Call once, with")
    L.append(" *  interrupts disabled (StartupHook via os_gen.h). */")
    L.append("void Rte_Init(void);")
    L.append("")
    L.append("/** One activation: read input ports <- BSW, run the runnable,")
    L.append(" *  write output ports -> BSW. */")
    L.append(f"void Rte_Run_{rm.name}(void);")
    L.append("")
    L.append("#endif /* RTE_H */")
    return "\n".join(L) + "\n"


def emit_rte_c(rm, src_name, integrated=False):
    headers = []
    for port in rm.inputs + rm.outputs:
        h = _DRIVER_HEADER.get(port.driver)
        if h and h not in headers:
            headers.append(h)

    L = []
    L.append("/**")
    L.append(" * @file    Rte.c")
    L.append(f" * @brief   {GENERATED_BANNER.format(src=src_name)}")
    L.append(" *")
    L.append(f" * RTE implementation for the '{rm.name}' model<->OS integration:")
    L.append(" * port adapters (IoHwAb) + lifecycle. Touches the ASW only through")
    L.append(" * its exported interface globals and the BSW through driver APIs.")
    L.append(" */")
    L.append("")
    L.append("#include <avr/io.h>")
    L.append("")
    L.append('#include "Rte.h"')
    L.append('#include "Rte_Cfg.h"')
    L.append("")
    L.append("/* ASW - generated model (read-only) */")
    L.append(f'#include "{rm.name}.h"')
    L.append(f'#include "{rm.name}_Intfc.h"')
    if headers:
        L.append("")
        L.append("/* BSW - MCAL drivers (read-only) */")
        for h in headers:
            L.append(f'#include "{h}"')
    L.append("")
    if integrated:
        L.append("/* OS: the runnable is wrapped as an EROS task (Task_<model>). */")
        L.append('#include "eros.h"')
        L.append('#include "config.h"')
    else:
        L.append("/* OS binding headers - only in a full-OS build (see Rte_Start). */")
        L.append("#ifdef RTE_WITH_EROS")
        L.append('#include "eros.h"')
        L.append('#include "config.h"')
        L.append("#endif")
    L.append("")
    L.append("/* --- Port adapters (IoHwAb): BSW signals <-> ASW ports ---------- */")
    L.append("")
    for port in rm.inputs + rm.outputs:
        L.extend(_adapter(port))
        L.append("")
    L.append("/* --- Lifecycle ------------------------------------------------- */")
    L.append("")
    L.append("void Rte_Init(void)")
    L.append("{")
    L.append("    /* BSW init for the bound ports. */")
    for port in rm.inputs + rm.outputs:
        if port.driver == "adc":
            L.append("    ADC_Init();")
        elif port.driver == "pwm":
            L.append("    PWM_Init();")
        elif port.driver == "dio":
            tag = port.tag
            if port.direction == "out":
                L.append(f"    RTE_CFG_{tag}_DDR  |= (uint8_t)(1u << RTE_CFG_{tag}_BIT);")
                L.append(f"    RTE_CFG_{tag}_PORT &= (uint8_t)~(1u << RTE_CFG_{tag}_BIT);")
            else:
                L.append(f"    RTE_CFG_{tag}_DDR  &= (uint8_t)~(1u << RTE_CFG_{tag}_BIT);")
    L.append("")
    L.append("    /* ASW init. */")
    L.append("    RTE_CFG_INIT_FN();")
    L.append("}")
    L.append("")
    L.append(f"void Rte_Run_{rm.name}(void)")
    L.append("{")
    if rm.inputs:
        L.append("    /* implicit read: sensor ports <- BSW */")
        for port in rm.inputs:
            L.append(f"    RTE_CFG_{port.tag}_SIGNAL = Rte_Read_{port.stem}();")
        L.append("")
    L.append("    /* run the ASW runnable */")
    L.append("    RTE_CFG_RUNNABLE_FN();")
    if rm.outputs:
        L.append("")
        L.append("    /* implicit write: actuator ports -> BSW */")
        for port in rm.outputs:
            L.append(f"    Rte_Write_{port.stem}(RTE_CFG_{port.tag}_SIGNAL);")
    L.append("}")
    L.append("")
    if integrated:
        L.append("/* --- OS task body: the cyclic alarm (armed by os_gen.h) ---------- */")
        L.append("/* activates this task; it runs one pass and terminates. --------- */")
        L.append("")
        L.append(f"void Task_{rm.name}(void)")
        L.append("{")
        L.append(f"    Rte_Run_{rm.name}();")
        L.append("    TerminateTask();")
        L.append("}")
    else:
        L.append("/* --- OS binding (production build only) ------------------------- */")
        L.append("")
        L.append("#ifdef RTE_WITH_EROS")
        L.append("void Rte_Start(void)")
        L.append("{")
        L.append(f"    (void)SetRelAlarm(ALARM_{rm.name.upper()},")
        L.append("                      RTE_CFG_PERIOD_MS, RTE_CFG_PERIOD_MS);")
        L.append("}")
        L.append("#endif")
    return "\n".join(L) + "\n"
