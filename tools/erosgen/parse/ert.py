"""Parse Embedded Coder ERT interface headers into a model interface model.

This relies on the ASW authoring *contract*: the model is generated with
per-signal ExportToFile / Define custom storage classes, which yields a narrow,
regular surface -

  <model>_Intfc.h :  extern <rtwtype> <IN_*|OUT_*>;           (ports)
  <model>_Param.h :  #define <NAME> <value>                   (Define params)
                     extern <rtwtype> <NAME>;                 (ExportToFile params)
  <model>.h       :  extern void <model>_initialize(void);    (entry points)
                     extern void <model>_Runnable(void);

so a tight regex is safe and dependency-free. We deliberately do NOT run a
general C frontend (pycparser/clang) on raw rtw output. Semantic metadata not
present in C (scaling, min/max) would come from codeInfo.mat - added behind the
[mat] extra only when bind.py needs it, not here.
"""
import re
from dataclasses import dataclass
from pathlib import Path

# Simulink built-in type -> (C stdint type, bit width, is_signed, is_bool).
RTW_TYPES = {
    "boolean_T": ("uint8_t", 8, False, True),
    "uint8_T":   ("uint8_t", 8, False, False),
    "int8_T":    ("int8_t", 8, True, False),
    "uint16_T":  ("uint16_t", 16, False, False),
    "int16_T":   ("int16_t", 16, True, False),
    "uint32_T":  ("uint32_t", 32, False, False),
    "int32_T":   ("int32_t", 32, True, False),
    "real32_T":  ("float", 32, True, False),
    "real_T":    ("double", 64, True, False),
}

# Line-anchored (re.M), horizontal-space only so a match never crosses a newline
# (which would let an include guard's #define swallow the next line's token).
# extern <ctype> <name>[ [dim] ];  - trailing comment ignored by the ; anchor.
_EXTERN_DATA = re.compile(
    r"^[ \t]*extern[ \t]+(\w+)[ \t]+(\w+)[ \t]*(?:\[(\d+)\])?[ \t]*;", re.M)
# extern void <name>(void);
_EXTERN_FUNC = re.compile(
    r"^[ \t]*extern[ \t]+void[ \t]+(\w+)[ \t]*\([ \t]*void[ \t]*\)[ \t]*;", re.M)
# #define <NAME> <value>   (value token required -> skips include guards)
_DEFINE = re.compile(r"^[ \t]*#define[ \t]+(\w+)[ \t]+(\S+)", re.M)


@dataclass(frozen=True)
class Signal:
    name: str          # e.g. "IN_KnbVal_Z"
    ctype: str         # rtw type, e.g. "uint16_T"
    direction: str     # "in" | "out" | "?" (unknown prefix)
    dim: int = 1       # array length; 1 == scalar
    description: str = ""  # hand-authored ASW only -> emitted as a C comment


@dataclass(frozen=True)
class Calibration:
    name: str
    ctype: str         # rtw type for extern params; "" for #define macros
    kind: str          # "extern" (ExportToFile) | "define" (Define)
    value: str = ""    # macro value for kind == "define"
    description: str = ""  # hand-authored ASW only -> emitted as a C comment


@dataclass(frozen=True)
class ModelInterface:
    name: str
    init_fn: str                 # <model>_initialize (or "" if absent)
    runnable_fns: tuple          # other extern void <fn>(void) entry points
    signals: tuple               # Signal[...]
    calibrations: tuple          # Calibration[...]

    @property
    def inputs(self):
        return tuple(s for s in self.signals if s.direction == "in")

    @property
    def outputs(self):
        return tuple(s for s in self.signals if s.direction == "out")

    def signal(self, name):
        return next((s for s in self.signals if s.name == name), None)


def _direction(name):
    if name.startswith("IN_"):
        return "in"
    if name.startswith("OUT_"):
        return "out"
    return "?"


def _parse_signals(text):
    out = []
    for m in _EXTERN_DATA.finditer(text):
        ctype, name, dim = m.group(1), m.group(2), m.group(3)
        out.append(Signal(name, ctype, _direction(name),
                          int(dim) if dim else 1))
    return out


def _parse_calibrations(text, guard):
    out = []
    for m in _EXTERN_DATA.finditer(text):
        out.append(Calibration(m.group(2), m.group(1), "extern"))
    for m in _DEFINE.finditer(text):
        name, value = m.group(1), m.group(2)
        if name == guard or name.endswith("_h_"):
            continue  # include guard, not a parameter
        out.append(Calibration(name, "", "define", value))
    return out


def parse_model(codegen_dir, model_name):
    """Parse <codegen_dir>/<model>{_Intfc,_Param}.h + <model>.h into a
    ModelInterface. Raises FileNotFoundError with a clear message if the
    expected ExportToFile headers are absent (the contract is violated)."""
    d = Path(codegen_dir)

    def read(suffix):
        p = d / f"{model_name}{suffix}"
        if not p.exists():
            raise FileNotFoundError(
                f"erosgen: model '{model_name}': expected {p} - is the model "
                "generated with ExportToFile/Define storage classes? "
                "(see rte/README_ASW.md)")
        return p.read_text()

    intfc = read("_Intfc.h")
    param = read("_Param.h")
    main = read(".h")

    signals = tuple(_parse_signals(intfc))
    calibrations = tuple(_parse_calibrations(param, f"{model_name}_Param_h_"))

    funcs = [m.group(1) for m in _EXTERN_FUNC.finditer(main)]
    init_fn = next((f for f in funcs if f == f"{model_name}_initialize"), "")
    runnables = tuple(f for f in funcs if f != init_fn)

    return ModelInterface(model_name, init_fn, runnables, signals, calibrations)
