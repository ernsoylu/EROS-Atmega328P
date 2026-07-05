"""Filesystem anchors that must stay stable across the package refactor."""
import os

# The invoked CLI entrypoint: the shim ``tools/erosgen.py`` next to this
# package. The generated Makefile's ``config:`` target reruns THIS path, so it
# must not change when emitters move between modules - otherwise the Makefile
# golden drifts. Computed (not hardcoded) but resolves to tools/erosgen.py.
ENTRYPOINT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "erosgen.py"))
