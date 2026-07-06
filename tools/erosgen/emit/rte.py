"""Emitters for the RTE: Rte_Cfg.h (declarative binding) and Rte.c (adapters).

Generated from a ResolvedModel (models.py). Mirrors the hand-written rte/
template (see rte/README.md) so the output compiles against the same drivers:
ADC_Read/ADC_Init for adc ports, DDRx/PORTx/PINx for dio ports. Ports with a
driver this emitter doesn't handle yet produce a visible #error.
"""

from ..backends import bit_clear, bit_read, bit_set, dio_direction_init
from ..constants import GENERATED_BANNER
from ..parse.ert import RTW_TYPES

_DRIVER_HEADER = {"adc": "adc.h", "pwm": "pwm.h"}  # dio uses raw avr/io.h

_DEF_PAD = 26  # macro-name column so every #define value aligns


def _def(name, value):
    return f"#define {name:<{_DEF_PAD}} {value}"


def _c_num(v):
    """A slope/offset as a C literal: an int stays an int, a float gets a
    real32 'f' suffix so the arithmetic stays single-precision on the AVR."""
    return str(v) if isinstance(v, int) else f"{v:.7g}f"


def _signal_ctype(signal, fallback):
    """The C stdint type for a signal's rtw type (e.g. uint16_T -> uint16_t)."""
    info = RTW_TYPES.get(signal.ctype)
    return info[0] if info else fallback


def _wide(port):
    """The arithmetic type for a calibration: int32_t (no 16-bit overflow) when
    slope and offset are both ints, else single-precision float."""
    both_int = isinstance(port.slope, int) and isinstance(port.offset, int)
    return "int32_t" if both_int else "float"


def _scale_note(port):
    """The '(scaled: ...)' comment suffix for a calibrated port (else '')."""
    if not port.scaled:
        return ""
    lhs, rhs = ("port", "raw") if port.direction == "in" else ("driver", "port")
    return f" (scaled: {lhs} = {rhs}*slope + offset)"


def _models(models):
    """Accept a single ResolvedModel or a list; return (list, multi?). One
    model emits the historical flat names (RTE_CFG_INIT_FN, ...) byte-for-byte;
    two or more namespace every identity define by model to avoid collisions."""
    rms = models if isinstance(models, list) else [models]
    return rms, len(rms) > 1


def _id_name(suffix, rm, multi):
    """A per-model identity #define name, e.g. RTE_CFG_INIT_FN (single model) or
    RTE_CFG_APPKNBSWT_INIT_FN (multi-model, namespaced by SWC)."""
    return f"RTE_CFG_{rm.name.upper()}_{suffix}" if multi else f"RTE_CFG_{suffix}"


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
    if port.scaled:
        L.append(_def(f"RTE_CFG_{tag}_SLOPE", _c_num(port.slope)))
        L.append(_def(f"RTE_CFG_{tag}_OFFSET", _c_num(port.offset)))
    return L


def _cfg_one(L, rm, multi):
    """One SWC's Rte_Cfg.h block: identity + port defines + rate (no guard)."""
    L.append("/* ---- SWC identity (ASW entry points) ---------------------------- */")
    L.append(_def(_id_name("INIT_FN", rm, multi), rm.init_fn))
    L.append(_def(_id_name("RUNNABLE_FN", rm, multi), rm.runnable_fn))
    L.append("")
    if rm.inputs:
        L.append("/* ---- Input ports: BSW sensor -> ASW port ------------------------ */")
        for port in rm.inputs:
            L.append(f"/* {port.signal.name} ({port.signal.ctype}) <- "
                     f"{port.driver}{_scale_note(port)} */")
            L.extend(_cfg_defines(port))
        L.append("")
    if rm.outputs:
        L.append("/* ---- Output ports: ASW port -> BSW actuator --------------------- */")
        for port in rm.outputs:
            L.append(f"/* {port.signal.name} ({port.signal.ctype}) -> "
                     f"{port.driver}{_scale_note(port)} */")
            L.extend(_cfg_defines(port))
        L.append("")
    L.append("/* ---- Scheduling: runnable rate assigned to the OS -------------- */")
    L.append(_def(_id_name("PERIOD_MS", rm, multi), f"{int(rm.rate_ms)}u"))


def emit_rte_cfg_h(models, src_name):
    rms, multi = _models(models)
    L = []
    L.append("/**")
    L.append(" * @file    Rte_Cfg.h")
    L.append(f" * @brief   {GENERATED_BANNER.format(src=src_name)}")
    L.append(" *")
    if multi:
        names = ", ".join(f"'{rm.name}'" for rm in rms)
        L.append(f" * Declarative ASW<->BSW binding for {len(rms)} SWCs ({names}):")
        L.append(" * which driver feeds each input port, which each output port")
        L.append(" * actuates, and each runnable rate. Pure configuration, no logic.")
    else:
        L.append(f" * Declarative ASW<->BSW binding for the '{rms[0].name}' SWC: which")
        L.append(" * driver feeds each input port, which each output port actuates,")
        L.append(" * and the runnable rate. Pure configuration, no logic.")
    L.append(" */")
    L.append("")
    L.append("#ifndef RTE_CFG_H")
    L.append("#define RTE_CFG_H")
    L.append("")
    for i, rm in enumerate(rms):
        if multi:
            if i:
                L.append("")
            L.append(f"/* ================= SWC: {rm.name} "
                     "================= */")
        _cfg_one(L, rm, multi)
    L.append("")
    L.append("#endif /* RTE_CFG_H */")
    return "\n".join(L) + "\n"


def _adapter(port):
    """The Rte_Read_*/Rte_Write_* adapter definition for one bound port."""
    tag, stem = port.tag, port.stem
    if port.driver == "adc":
        if port.scaled:
            # opt-in linear calibration: port = raw*slope + offset. Integer
            # math (via int32_t) when both are ints, single-precision float
            # otherwise; either way the constants live in Rte_Cfg.h.
            cty = _signal_ctype(port.signal, "uint16_t")
            wide = _wide(port)
            return [f"static {cty} Rte_Read_{stem}(void)",
                    "{",
                    f"    uint16_t raw = ADC_Read(RTE_CFG_{tag}_ADC_CH);",
                    f"    return ({cty})(({wide})raw * RTE_CFG_{tag}_SLOPE"
                    f" + RTE_CFG_{tag}_OFFSET);",
                    "}"]
        return [f"static uint16_t Rte_Read_{stem}(void)",
                "{",
                f"    return ADC_Read(RTE_CFG_{tag}_ADC_CH);",
                "}"]
    if port.driver == "dio" and port.direction == "in":
        return [f"static uint8_t Rte_Read_{stem}(void)",
                "{",
                f"    return {bit_read(f'RTE_CFG_{tag}_PIN', f'RTE_CFG_{tag}_BIT')};",
                "}"]
    if port.driver == "dio" and port.direction == "out":
        return [f"static void Rte_Write_{stem}(uint8_t on)",
                "{",
                "    if (on)",
                "    {",
                f"        {bit_set(f'RTE_CFG_{tag}_PORT', f'RTE_CFG_{tag}_BIT')};",
                "    }",
                "    else",
                "    {",
                f"        {bit_clear(f'RTE_CFG_{tag}_PORT', f'RTE_CFG_{tag}_BIT')};",
                "    }",
                "}"]
    if port.driver == "pwm" and port.direction == "out":
        if port.scaled:
            # opt-in calibration: permille = port*slope + offset (the ASW value
            # in engineering units -> the driver's 0..1000 permille).
            ptype = _signal_ctype(port.signal, "uint16_t")
            wide = _wide(port)
            return [f"static void Rte_Write_{stem}({ptype} value)",
                    "{",
                    f"    uint16_t permille = (uint16_t)(({wide})value"
                    f" * RTE_CFG_{tag}_SLOPE + RTE_CFG_{tag}_OFFSET);",
                    "    PWM_SetDutyPermille(permille);",
                    "}"]
        return [f"static void Rte_Write_{stem}(uint16_t permille)",
                "{",
                "    PWM_SetDutyPermille(permille);",
                "}"]
    return [f'#error "RTE emit: driver \'{port.driver}\' '
            f'({port.direction}) not supported yet"']


def emit_rte_h(models, src_name):
    rms, multi = _models(models)
    L = []
    L.append("/**")
    L.append(" * @file    Rte.h")
    L.append(f" * @brief   {GENERATED_BANNER.format(src=src_name)}")
    L.append(" *")
    if multi:
        names = ", ".join(f"'{rm.name}'" for rm in rms)
        L.append(f" * RTE public API for the {names} model<->OS integration.")
    else:
        L.append(f" * RTE public API for the '{rms[0].name}' model<->OS integration.")
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
    for rm in rms:
        L.append(f"void Rte_Run_{rm.name}(void);")
    L.append("")
    L.append("#endif /* RTE_H */")
    return "\n".join(L) + "\n"


def _rte_init(L, rms, multi):
    """The single Rte_Init: BSW init for every model's bound ports, then each
    model's ASW init. One model reproduces the historical body byte-for-byte."""
    L.append("void Rte_Init(void)")
    L.append("{")
    for idx, rm in enumerate(rms):
        if multi:
            if idx:
                L.append("")
            L.append(f"    /* {rm.name} */")
        L.append("    /* BSW init for the bound ports. */")
        for port in rm.inputs + rm.outputs:
            if port.driver == "adc":
                L.append("    ADC_Init();")
            elif port.driver == "pwm":
                L.append("    PWM_Init();")
            elif port.driver == "dio":
                for line in dio_direction_init(port.tag, port.direction == "out"):
                    L.append(f"    {line}")
        L.append("")
        L.append("    /* ASW init. */")
        L.append(f"    {_id_name('INIT_FN', rm, multi)}();")
    L.append("}")


def _rte_run(L, rm, multi):
    """One model's Rte_Run_<model>: read input ports, run the runnable, write
    output ports. An internal input is copied straight from the producing SWC's
    output global; an internal-only output has no BSW write (its global is left
    for a downstream SWC to read)."""
    L.append(f"void Rte_Run_{rm.name}(void)")
    L.append("{")
    if rm.inputs:
        # keep the historical comment when nothing is internal (goldens), widen
        # it only when an ASW<->ASW input is present.
        L.append("    /* implicit read: input ports <- BSW / producer SWCs */"
                 if any(p.internal for p in rm.inputs)
                 else "    /* implicit read: sensor ports <- BSW */")
        for port in rm.inputs:
            if port.internal:
                L.append(f"    RTE_CFG_{port.tag}_SIGNAL = {port.source_signal};"
                         f"  /* <- {port.source} */")
            else:
                L.append(f"    RTE_CFG_{port.tag}_SIGNAL = "
                         f"Rte_Read_{port.stem}();")
        L.append("")
    L.append("    /* run the ASW runnable */")
    L.append(f"    {_id_name('RUNNABLE_FN', rm, multi)}();")
    hw_outs = [p for p in rm.outputs if not p.internal]
    if hw_outs:
        L.append("")
        L.append("    /* implicit write: actuator ports -> BSW */")
        for port in hw_outs:
            L.append(f"    Rte_Write_{port.stem}(RTE_CFG_{port.tag}_SIGNAL);")
    L.append("}")


def emit_rte_c(models, src_name, integrated=False):
    rms, multi = _models(models)
    all_ports = [p for rm in rms for p in rm.inputs + rm.outputs]
    headers = []
    for port in all_ports:
        h = _DRIVER_HEADER.get(port.driver)
        if h and h not in headers:
            headers.append(h)

    L = []
    L.append("/**")
    L.append(" * @file    Rte.c")
    L.append(f" * @brief   {GENERATED_BANNER.format(src=src_name)}")
    L.append(" *")
    if multi:
        names = ", ".join(f"'{rm.name}'" for rm in rms)
        L.append(f" * RTE implementation for the {names} model<->OS integration:")
    else:
        L.append(f" * RTE implementation for the '{rms[0].name}' model<->OS integration:")
    L.append(" * port adapters (IoHwAb) + lifecycle. Touches the ASW only through")
    L.append(" * its exported interface globals and the BSW through driver APIs.")
    L.append(" */")
    L.append("")
    L.append("#include <avr/io.h>")
    L.append("")
    L.append('#include "Rte.h"')
    L.append('#include "Rte_Cfg.h"')
    L.append("")
    L.append(f"/* ASW - generated model{'s' if multi else ''} (read-only) */")
    for rm in rms:
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
    for port in all_ports:
        if port.internal:
            continue          # ASW<->ASW: copied directly in Rte_Run, no adapter
        L.extend(_adapter(port))
        L.append("")
    L.append("/* --- Lifecycle ------------------------------------------------- */")
    L.append("")
    _rte_init(L, rms, multi)
    for rm in rms:
        L.append("")
        _rte_run(L, rm, multi)
    L.append("")
    if integrated:
        L.append("/* --- OS task body: the cyclic alarm (armed by os_gen.h) ---------- */")
        L.append("/* activates this task; it runs one pass and terminates. --------- */")
        for rm in rms:
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
        for rm in rms:
            period = _id_name("PERIOD_MS", rm, multi)
            L.append(f"    (void)SetRelAlarm(ALARM_{rm.name.upper()},")
            L.append(f"                      {period}, {period});")
        L.append("}")
        L.append("#endif")
    return "\n".join(L) + "\n"
