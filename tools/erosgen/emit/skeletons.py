"""Emitters for the "once" skeletons: per-rate asw_<n>ms.c and main.c.

These are written only when absent (see cli.write, overwrite=False), so a
hand-edited ASW/main is never clobbered.
"""

from ..constants import INCLUDE_EROS_H


def emit_asw_skeleton(s, task):
    step_call = None
    if s.simulink:
        for step, tname in (s.simulink.get("rate_map") or {}).items():
            if str(tname).upper() == task.name:
                step_call = f"{s.simulink['model']}_{step}();"
    L = []
    L.append("/**")
    L.append(f" * @file    asw_{task.period_ms}ms.c")
    L.append(f" * @brief   {task.period_ms} ms rate - TASK_{task.name}.")
    L.append(" *")
    L.append(" * Generated once by tools/erosgen.py - edit freely; it will not")
    L.append(" * be overwritten. Keep rate-local state static in this file;")
    L.append(" * cross-rate signals belong in an asw_signals module.")
    L.append(" */")
    L.append("")
    L.append(INCLUDE_EROS_H)
    if s.simulink:
        L.append(f'#include "{s.simulink["model"]}.h"')
    L.append("")
    if task.runnables:
        L.append("/* Runnables you implement (here or in another TU). Move")
        L.append(" * these prototypes to a shared header if reused. */")
        for r in task.runnables:
            L.append(f"extern void {r}(void);")
        L.append("")
    L.append(f"/** TASK_{task.name} - {task.period_ms} ms, WCET <= "
             f"{task.wcet_ticks} tick(s). */")
    L.append(f"void {task.entry}(void)")
    L.append("{")
    if step_call:
        L.append("    /* sample: hardware -> model inports here */")
        L.append(f"    {step_call}")
        L.append("    /* actuate: model outports -> hardware here */")
    for r in task.runnables:
        L.append(f"    {r}();")
    if not step_call and not task.runnables:
        L.append("    /* TODO: runnables for this rate */")
    L.append("    TerminateTask();")
    L.append("}")
    return "\n".join(L) + "\n"


def emit_main_skeleton(s):
    init = next((t for t in s.tasks if t.autostart), None)
    L = []
    L.append("/**")
    L.append(" * @file    main.c")
    L.append(" * @brief   Integration layer: hooks + init task + main().")
    L.append(" *")
    L.append(" * Generated once by tools/erosgen.py - edit freely. Pin setup")
    L.append(" * and alarm arming live in os_gen.h (regenerated every run).")
    L.append(" */")
    L.append("")
    L.append(INCLUDE_EROS_H)
    L.append('#include "os_gen.h"')
    L.append("")
    if s.hook_startup:
        L.append("void StartupHook(void)")
        L.append("{")
        L.append("    Board_ConfigurePins(); /* generated: gpio + driver init */")
        L.append("}")
        L.append("")
    if s.hook_error:
        L.append("void ErrorHook(StatusType error)")
        L.append("{")
        L.append("    (void)error; /* may run in tick-ISR context: stay tiny */")
        L.append("}")
        L.append("")
    if s.hook_shutdown:
        L.append("void ShutdownHook(StatusType error)")
        L.append("{")
        L.append("    (void)error; /* terminal fault tombstone */")
        L.append("}")
        L.append("")
    if init is not None:
        L.append(f"/** TASK_{init.name} - autostart: arm the cyclic alarms. */")
        L.append(f"void {init.entry}(void)")
        L.append("{")
        if s.simulink:
            L.append(f"    {s.simulink['model']}_initialize();")
        L.append("    OS_StartAlarms(); /* generated from the YAML task set */")
        L.append("    TerminateTask();")
        L.append("}")
        L.append("")
    L.append("int main(void)")
    L.append("{")
    L.append("    StartOS(); /* noreturn */")
    L.append("}")
    return "\n".join(L) + "\n"
