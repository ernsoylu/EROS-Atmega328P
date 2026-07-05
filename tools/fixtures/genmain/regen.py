#!/usr/bin/env python3
"""Regenerate the genmain golden snapshots.

Run after an *intentional* emitter change so the golden test pins the new
output; never hand-edit the .golden files:

    uv run python tools/fixtures/genmain/regen.py
"""
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # tools/ -> import erosgen
import erosgen  # noqa: E402


def main():
    ymp = HERE / "app.yaml"
    s = erosgen.System(yaml.safe_load(ymp.read_text()), ymp)
    ctrl = s.periodic[0]  # only periodic task
    outputs = {
        "os_gen.h.golden":   erosgen.emit_os_gen_h(s),
        "main.c.golden":     erosgen.emit_main_skeleton(s),
        "asw_10ms.c.golden": erosgen.emit_asw_skeleton(s, ctrl),
    }
    for name, content in outputs.items():
        (HERE / name).write_text(content)
        print("wrote", name)


if __name__ == "__main__":
    main()
