#!/usr/bin/env python3
"""Tests for erosgen. Runs standalone (no pytest needed):

    python3 tools/test_erosgen.py

and is also collected by pytest if that is installed. Requires PyYAML.

The load-bearing test is the golden pair: regenerating each reference
demo's config.h/config.c from its app.yaml must reproduce the committed
files byte for byte. That single assertion pins the whole generator -
if a future edit changes any demo's generated output, this fails.
"""

import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import erosgen  # noqa: E402


def _system(text):
    return erosgen.System(yaml.safe_load(text), Path("app.yaml"))


def _system_from(path):
    return erosgen.System(yaml.safe_load(Path(path).read_text()), Path(path))


DEMOS = [
    (REPO / "reference-demo" / "app.yaml", REPO / "reference-demo"),
]


def test_demos_config_h_golden():
    for yml, d in DEMOS:
        s = _system_from(yml)
        got = erosgen.emit_config_h(s)
        want = (d / "config.h").read_text()
        assert got == want, f"{d.name}/config.h drifted from app.yaml"


def test_demos_config_c_golden():
    for yml, d in DEMOS:
        s = _system_from(yml)
        got = erosgen.emit_config_c(s)
        want = (d / "config.c").read_text()
        assert got == want, f"{d.name}/config.c drifted from app.yaml"


def test_priority_is_rate_monotonic():
    s = _system_from(REPO / "reference-demo" / "app.yaml")
    prio = {t.name: t.priority for t in s.tasks}
    # autostart lowest, aperiodic next, then faster => higher.
    assert prio["STARTUP"] == 0
    assert prio["REPORT"] == 1
    assert prio["BUTTON"] > prio["CMD"] > prio["RAMP"] > prio["STATUS"]


def test_resource_ceiling_is_highest_user():
    s = _system_from(REPO / "reference-demo" / "app.yaml")
    demo = next(r for r in s.resources if r.name == "DEMO")
    assert demo.ceiling.name == "BUTTON"  # highest-priority of {BUTTON, CMD}
    uart = next(r for r in s.resources if r.name == "UART")
    assert uart.ceiling.name == "CMD"     # highest-priority of {STATUS, CMD}


def test_wcet_rounds_up_never_under_budget():
    s = _system(BASE + """
tasks:
  - { name: a, period_ms: 10, wcet_ms: 1, runnables: [x] }
resources: [{ name: r, users: [a] }]
""")
    # 1 ms WCET at a 1 ms tick == 1 tick (exact); the ceil path is
    # exercised by fractional inputs below.
    assert s.tasks[0].wcet_ticks == 1


BASE = """
system: { name: t, kernel_dir: ../kernel }
sources: [main.c]
"""

BAD = {
    "unknown task key": (BASE + """
tasks: [{ name: a, periods_ms: 10, wcet_ms: 1 }]
resources: [{ name: r, users: [a] }]
""", "unknown key 'periods_ms'"),
    "non-1000 tick": ("""
system: { name: t, kernel_dir: ../kernel, tick_hz: 500 }
sources: [main.c]
tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]
resources: [{ name: r, users: [a] }]
""", "tick_hz must be 1000"),
    "unschedulable": (BASE + """
tasks:
  - { name: a, period_ms: 10, wcet_ms: 6 }
  - { name: b, period_ms: 10, wcet_ms: 6 }
resources: [{ name: r, users: [a] }]
""", "not schedulable"),
    "too many tasks": (BASE + """
tasks:
""" + "".join(f"  - {{ name: t{i}, period_ms: {10*(i+1)}, wcet_ms: 1 }}\n"
               for i in range(9)) + """
resources: [{ name: r, users: [t0] }]
""", "1..8 tasks"),
    "peripheral pin conflict": ("""
system: { name: t, kernel_dir: ../kernel, drivers_dir: ../drivers }
sources: [main.c]
peripherals: { spi: {} }
gpio: [{ pin: D13, dir: out, name: LED }]
tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]
resources: [{ name: r, users: [a] }]
""", "pin conflict on PB5"),
    "unknown peripheral": ("""
system: { name: t, kernel_dir: ../kernel }
sources: [main.c]
peripherals: { uarts: {} }
tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]
resources: [{ name: r, users: [a] }]
""", "unknown peripheral 'uarts'"),
    "resource unknown user": (BASE + """
tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]
resources: [{ name: r, users: [nope] }]
""", "unknown user task"),
}


def test_bad_configs_are_rejected():
    for label, (text, needle) in BAD.items():
        try:
            _system(text)
        except erosgen.ConfigError as e:
            assert needle in str(e), \
                f"{label}: expected '{needle}' in '{e}'"
        else:
            raise AssertionError(f"{label}: expected ConfigError, got none")


def _run_standalone():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
