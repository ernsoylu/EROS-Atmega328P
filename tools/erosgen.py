#!/usr/bin/env python3
"""Shim entrypoint for the erosgen package.

    python3 tools/erosgen.py <app.yaml> [--check]

The 1000-line monolith was split into the erosgen/ package (model / emit / mcu /
validate / cli). This path is kept stable on purpose: generated Makefiles'
``config:`` target reruns it, and callers ``import erosgen``. When executed
directly, Python puts this file's directory (tools/) on sys.path, so the bare
``erosgen`` below resolves to the sibling erosgen/ package (a package shadows a
same-named module), not to this shim.
"""
import sys

from erosgen.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
