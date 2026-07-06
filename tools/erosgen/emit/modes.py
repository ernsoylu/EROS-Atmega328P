"""Emitter for a generated mode-management primitive (Rte_Modes.h/.c).

A `modes:` section declares mode groups; each becomes a typed C enum plus a
current-mode variable with `Rte_Mode_<grp>()` (get) / `Rte_Switch_<grp>()` (set)
accessors — a shared, type-safe mode one task sets and others query. This is the
minimal AUTOSAR mode primitive; OnEntry/OnExit runnables and transition tables
are deliberately out of scope for this non-preemptive AVR kernel (tasks chain
via `ChainTask`, so a mode variable is all the coordination they need).
"""
from ..constants import GENERATED_BANNER


def _enum_type(grp):
    return f"Rte_ModeType_{grp}"


def _const(grp, state):
    return f"RTE_MODE_{grp.upper()}_{state.upper()}"


def emit_modes_h(modes, src_name):
    L = ["/**", " * @file    Rte_Modes.h",
         f" * @brief   {GENERATED_BANNER.format(src=src_name)}", " *",
         " * Generated mode groups: a typed enum + get/switch accessors per",
         " * group. Include this to query (Rte_Mode_<grp>) or request a switch",
         " * (Rte_Switch_<grp>) of the current mode.", " */", "",
         "#ifndef RTE_MODES_H", "#define RTE_MODES_H", ""]
    for m in modes:
        grp, states = m["name"], m["states"]
        L.append("typedef enum")
        L.append("{")
        for i, st in enumerate(states):
            comma = "," if i < len(states) - 1 else ""
            L.append(f"    {_const(grp, st)}{comma}")
        L.append(f"}} {_enum_type(grp)};")
        L.append("")
        L.append(f"/** The current '{grp}' mode. */")
        L.append(f"{_enum_type(grp)} Rte_Mode_{grp}(void);")
        L.append(f"/** Request a '{grp}' mode switch. */")
        L.append(f"void Rte_Switch_{grp}({_enum_type(grp)} mode);")
        L.append("")
    L.append("#endif /* RTE_MODES_H */")
    return "\n".join(L) + "\n"


def emit_modes_c(modes, src_name):
    L = ["/**", " * @file    Rte_Modes.c",
         f" * @brief   {GENERATED_BANNER.format(src=src_name)}", " */", "",
         '#include "Rte_Modes.h"', ""]
    for m in modes:
        grp = m["name"]
        init = m.get("initial") or m["states"][0]
        L.append(f"static {_enum_type(grp)} rte_mode_{grp} = {_const(grp, init)};")
        L.append("")
        L.append(f"{_enum_type(grp)} Rte_Mode_{grp}(void)")
        L.append("{")
        L.append(f"    return rte_mode_{grp};")
        L.append("}")
        L.append("")
        L.append(f"void Rte_Switch_{grp}({_enum_type(grp)} mode)")
        L.append("{")
        L.append(f"    rte_mode_{grp} = mode;")
        L.append("}")
        L.append("")
    return "\n".join(L).rstrip("\n") + "\n"
