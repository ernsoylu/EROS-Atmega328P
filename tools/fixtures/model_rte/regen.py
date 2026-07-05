#!/usr/bin/env python3
"""Regenerate the model_rte RTE golden snapshots (Rte_Cfg.h / Rte.c).

Run after an intentional emitter change; never hand-edit the .golden files:

    uv run python tools/fixtures/model_rte/regen.py
"""
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # tools/ -> import erosgen
from erosgen import Diagnostics                       # noqa: E402
from erosgen.emit.rte import emit_rte_c, emit_rte_cfg_h  # noqa: E402
from erosgen.models import resolve_models             # noqa: E402


def main():
    doc = yaml.safe_load((HERE / "app.yaml").read_text())
    sink = Diagnostics(strict=True)  # a broken fixture must fail loudly
    rm = resolve_models(doc, HERE, sink)[0]
    (HERE / "Rte_Cfg.h.golden").write_text(emit_rte_cfg_h(rm, "app.yaml"))
    (HERE / "Rte.c.golden").write_text(emit_rte_c(rm, "app.yaml"))
    print("wrote Rte_Cfg.h.golden, Rte.c.golden")


if __name__ == "__main__":
    main()
