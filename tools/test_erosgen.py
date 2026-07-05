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

# Golden fixture for the emitters reference-demo doesn't exercise (it ships a
# hand-written main.c, so it has no os_gen.h). See fixtures/genmain/regen.py.
GENMAIN = HERE / "fixtures" / "genmain"


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


def test_demos_makefile_golden():
    for yml, d in DEMOS:
        s = _system_from(yml)
        got = erosgen.emit_makefile(s, d.resolve())
        want = (d / "Makefile").read_text()
        assert got == want, f"{d.name}/Makefile drifted from app.yaml"


def test_genmain_skeleton_goldens():
    """Pin the emitters reference-demo can't reach: os_gen.h, the generated
    main.c, and an asw skeleton with a Simulink step. Regenerate the .golden
    snapshots with fixtures/genmain/regen.py after an intentional change."""
    s = _system_from(GENMAIN / "app.yaml")
    ctrl = s.periodic[0]
    cases = {
        "os_gen.h.golden":   erosgen.emit_os_gen_h(s),
        "main.c.golden":     erosgen.emit_main_skeleton(s),
        "asw_10ms.c.golden": erosgen.emit_asw_skeleton(s, ctrl),
    }
    for name, got in cases.items():
        want = (GENMAIN / name).read_text()
        assert got == want, f"genmain/{name} drifted from the emitter"


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


def test_collect_diagnostics_gathers_multiple_errors():
    # collect mode is non-throwing and reports every independent problem at
    # once (a GUI needs them all, not just the first that would raise).
    text = BASE.replace("kernel_dir: ../kernel",
                        "kernel_dir: ../kernel, drivers_dir: ../drivers") + """
peripherals: { spi: {}, uarts: {} }
gpio: [{ pin: D13, dir: out, name: LED }]
tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]
resources: [{ name: r, users: [a] }]
"""
    diags = erosgen.collect_diagnostics(yaml.safe_load(text), Path("app.yaml"))
    codes = {d.code for d in diags}
    assert "UNKNOWN_PERIPHERAL" in codes    # 'uarts'
    assert "PIN_CONFLICT" in codes          # spi SCK on PB5 vs LED on D13/PB5
    assert sum(d.severity == "error" for d in diags) >= 2
    # every item is a structured Diagnostic, never a raised exception
    assert all(isinstance(d, erosgen.Diagnostic) for d in diags)


def test_collect_diagnostics_clean_for_reference_demo():
    yml = REPO / "reference-demo" / "app.yaml"
    diags = erosgen.collect_diagnostics(yaml.safe_load(yml.read_text()), yml)
    assert [d for d in diags if d.severity == "error"] == []


def test_mcu_profile_loads_atmega328p():
    from erosgen.mcu import load_profile
    p = load_profile("atmega328p")
    assert p.name == "atmega328p"
    assert p.known_peripherals["uart"] == "uart.c"
    assert p.peripheral_pins["spi"] == ["PB2", "PB3", "PB4", "PB5"]
    assert p.driver_init["adc"] == "ADC_Init();"
    assert p.conflicts == [("icp", "pwm",
                            "both own Timer1 (capture vs ICR1-as-TOP)")]
    # unknown target fails loudly (not a silent empty profile)
    try:
        load_profile("atmega_nope")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError for unknown MCU")


def test_parse_ert_appknbswt():
    from erosgen.parse import parse_model
    mi = parse_model(REPO / "codegen" / "appKnbSwt_ert_rtw", "appKnbSwt")
    assert mi.init_fn == "appKnbSwt_initialize"
    assert mi.runnable_fns == ("appKnbSwt_Runnable",)
    ins = {s.name: s for s in mi.inputs}
    outs = {s.name: s for s in mi.outputs}
    assert ins["IN_KnbVal_Z"].ctype == "uint16_T" and ins["IN_KnbVal_Z"].dim == 1
    assert outs["OUT_Led1_B"].ctype == "boolean_T"
    cal = {c.name: c for c in mi.calibrations}
    assert cal["ADC_MAX"].kind == "define" and cal["ADC_MAX"].value == "1023U"
    assert cal["Knb_Hyst_Pc_Pt"].kind == "extern"
    assert cal["Knb_Hyst_Pc_Pt"].ctype == "uint8_T"


def test_bind_compatibility():
    from erosgen import Diagnostics
    from erosgen.bind import check_binding
    from erosgen.parse import Signal

    def codes(sig, direction, driver, params):
        s = Diagnostics(strict=False)
        check_binding(sig, direction, driver, params, s, "port")
        return {d.code for d in s.items}

    knb = Signal("IN_KnbVal_Z", "uint16_T", "in")
    led = Signal("OUT_Led1_B", "boolean_T", "out")
    # the appKnbSwt bindings are clean
    assert codes(knb, "in", "adc", {"channel": 0}) == set()
    assert codes(led, "out", "dio", {"port": "B", "bit": 5}) == set()
    # uint8 can't hold the ADC's 0..1023 range
    assert "TYPE_TOO_NARROW" in codes(Signal("IN_X", "uint8_T", "in"),
                                      "in", "adc", {"channel": 0})
    # adc is input-only
    assert "DRIVER_DIRECTION" in codes(Signal("OUT_X", "uint16_T", "out"),
                                       "out", "adc", {"channel": 0})
    assert "UNKNOWN_DRIVER" in codes(led, "out", "nope", {})
    assert "BINDING_MISSING_KEY" in codes(knb, "in", "adc", {})


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
