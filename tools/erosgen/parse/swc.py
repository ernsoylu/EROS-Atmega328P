"""Tier-B ASW interchange: build a ModelInterface from a hand-authored swc.yaml.

A first-class alternative to the Embedded Coder round-trip (parse/ert.py) — the
same data model, authored directly instead of parsed from generated headers, so
a SWC can be integrated without Simulink/Embedded Coder at all::

  name:  appKnbSwt
  init:  appKnbSwt_initialize
  runnables: [appKnbSwt_Runnable]
  ports:
    in:  [{ signal: IN_KnbVal_Z, type: uint16_T }]
    out: [{ signal: OUT_Led1_B,  type: boolean_T }]
  calibrations:
    - { name: Knb_Thresh_Pc_Pt, type: uint8_T }   # extern (ExportToFile)
    - { name: ADC_MAX, value: "1023U" }           # #define (Define)
"""
from pathlib import Path

import yaml

from .ert import Calibration, ModelInterface, Signal


def parse_swc_yaml(path, model_name):
    """Load a swc.yaml into a ModelInterface. Raises FileNotFoundError if the
    file is absent, ValueError on a non-mapping document."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"erosgen: model '{model_name}': swc file {p} not found")
    doc = yaml.safe_load(p.read_text()) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: swc.yaml must be a mapping")

    name = doc.get("name", model_name)
    init_fn = doc.get("init", "")
    runnables = tuple(doc.get("runnables", []) or [])

    signals = []
    ports = doc.get("ports", {}) or {}
    for direction in ("in", "out"):
        for pd in (ports.get(direction, []) or []):
            if not isinstance(pd, dict) or not pd.get("signal"):
                continue
            signals.append(Signal(
                pd["signal"], pd.get("type", "uint8_T"), direction,
                int(pd.get("dim", 1)), pd.get("description", "")))

    cals = []
    for cd in (doc.get("calibrations", []) or []):
        if not isinstance(cd, dict) or not cd.get("name"):
            continue
        # a Define macro carries a `value` and no C type; an ExportToFile param
        # carries a `type` and lives in <name>_Param.c.
        if "value" in cd and "type" not in cd:
            cals.append(Calibration(cd["name"], "", "define",
                                    str(cd["value"]), cd.get("description", "")))
        else:
            cals.append(Calibration(cd["name"], cd.get("type", "uint8_T"),
                                    "extern", str(cd.get("value", "")),
                                    cd.get("description", "")))

    return ModelInterface(name, init_fn, runnables, tuple(signals), tuple(cals))
