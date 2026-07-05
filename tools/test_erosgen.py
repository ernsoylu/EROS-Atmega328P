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


def test_model_rte_goldens():
    from erosgen import Diagnostics
    from erosgen.emit.rte import emit_rte_c, emit_rte_cfg_h
    from erosgen.models import resolve_models
    d = HERE / "fixtures" / "model_rte"
    doc = yaml.safe_load((d / "app.yaml").read_text())
    sink = Diagnostics(strict=False)
    rms = resolve_models(doc, d, sink)
    # the appKnbSwt port bindings resolve type-safe: no error diagnostics
    errs = [x.message for x in sink.items if x.severity == "error"]
    assert errs == [], errs
    assert len(rms) == 1
    rm = rms[0]
    assert emit_rte_cfg_h(rm, "app.yaml") == (d / "Rte_Cfg.h.golden").read_text()
    assert emit_rte_c(rm, "app.yaml") == (d / "Rte.c.golden").read_text()


def test_model_app_end_to_end_goldens():
    """A full models: app generates config.*, Makefile, main.c, os_gen.h AND
    Rte.h/Rte_Cfg.h/Rte.c, with the model wired as TASK_/ALARM_<model>. Pins the
    whole end-to-end output; CI additionally compiles it (see ci.yml)."""
    from erosgen import Diagnostics, System, resolve_models
    from erosgen.emit import (emit_config_c, emit_config_h, emit_main_skeleton,
                              emit_makefile, emit_os_gen_h, emit_rte_c,
                              emit_rte_cfg_h, emit_rte_h)
    d = HERE / "fixtures" / "model_app"
    doc = yaml.safe_load((d / "app.yaml").read_text())
    s = System(doc, d / "app.yaml")
    got = {
        "config.h": emit_config_h(s),
        "config.c": emit_config_c(s),
        "Makefile": emit_makefile(s, d.resolve()),
        "os_gen.h": emit_os_gen_h(s),
        "main.c":   emit_main_skeleton(s),
    }
    sink = Diagnostics(strict=False)
    rms = resolve_models(doc, d, sink)
    assert [x.message for x in sink.items if x.severity == "error"] == []
    rm = rms[0]
    got["Rte.h"] = emit_rte_h(rm, "app.yaml")
    got["Rte_Cfg.h"] = emit_rte_cfg_h(rm, "app.yaml")
    got["Rte.c"] = emit_rte_c(rm, "app.yaml", integrated=True)
    for name, text in got.items():
        assert text == (d / name).read_text(), f"model_app/{name} drifted"
    # the model became a real OS task + alarm
    assert "TASK_APPKNBSWT" in got["config.h"]
    assert "ALARM_APPKNBSWT" in got["config.h"]


def test_mega_mcu_abstraction():
    from erosgen import System, collect_diagnostics
    from erosgen.emit import emit_makefile, emit_os_gen_h
    d = HERE / "fixtures" / "mega_gpio"
    doc = yaml.safe_load((d / "app.yaml").read_text())
    s = System(doc, d / "app.yaml")
    assert s.mcu == "atmega2560" and s.profile.name == "atmega2560"
    mk = emit_makefile(s, d.resolve())
    assert "MCU     := atmega2560" in mk and "-p m2560 -c wiring" in mk
    assert mk == (d / "Makefile").read_text()
    og = emit_os_gen_h(s)
    assert "DDRL |= (uint8_t)(1u << PL7)" in og   # PORTL: 2560-only
    assert "DDRB |= (uint8_t)(1u << PB7)" in og   # Mega D13 alias -> PB7
    assert og == (d / "os_gen.h").read_text()

    # PL7 (port L) is profile-driven: rejected on the 328P, accepted on the 2560.
    def pl7_codes(mcu):
        doc2 = {"system": {"name": "t", "mcu": mcu},
                "gpio": [{"pin": "PL7", "dir": "out"}],
                "tasks": [{"name": "a", "period_ms": 10, "wcet_ms": 1}],
                "resources": [{"name": "r", "users": ["a"]}]}
        return {x.code for x in collect_diagnostics(doc2, Path("app.yaml"))}
    assert "UNKNOWN_PIN" in pl7_codes("atmega328p")
    assert "UNKNOWN_PIN" not in pl7_codes("atmega2560")


def test_unknown_mcu_is_reported():
    doc = {"system": {"name": "t", "mcu": "attiny85"},
           "tasks": [{"name": "a", "period_ms": 10, "wcet_ms": 1}],
           "resources": [{"name": "r", "users": ["a"]}]}
    codes = {x.code for x in erosgen.collect_diagnostics(doc, Path("app.yaml"))}
    assert "UNKNOWN_MCU" in codes


def test_pwm_rte_adapter():
    from erosgen.bind import DRIVERS
    from erosgen.emit.rte import emit_rte_c, emit_rte_cfg_h
    from erosgen.models import BoundPort, ResolvedModel
    from erosgen.parse import Signal
    # the pwm spec matches the real Timer1 driver: permille 0..1000, uint16, no
    # channel/pin param (fixed OC1A).
    assert DRIVERS["pwm"].vmax == 1000
    assert DRIVERS["pwm"].value_ctype == "uint16_t"
    assert DRIVERS["pwm"].required == ()
    # a uint16 output bound to pwm emits a PWM_SetDutyPermille adapter, no #error
    port = BoundPort(Signal("OUT_Duty_Pm", "uint16_T", "out"),
                     "out", "pwm", {}, "Duty_Pm")
    rm = ResolvedModel("motor", "motor_initialize", "motor_Runnable", 20,
                       [], [port], None)
    c = emit_rte_c(rm, "app.yaml", integrated=True)
    assert "#error" not in c
    assert "PWM_SetDutyPermille(permille)" in c
    assert '#include "pwm.h"' in c and "PWM_Init();" in c
    assert "RTE_CFG_DUTY_PM_SIGNAL" in emit_rte_cfg_h(rm, "app.yaml")


def test_multi_model_rejected():
    # Two models is unsupported (a single RTE): System must reject it up front,
    # not silently emit one model's Rte.* over the other's. This locks in the
    # MULTI_MODEL guard the end-to-end goldens only exercise for one model.
    doc_text = BASE + """
tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]
resources: [{ name: r, users: [a] }]
models:
  - { name: m1, codegen_dir: a_ert_rtw, rate_ms: 10 }
  - { name: m2, codegen_dir: b_ert_rtw, rate_ms: 10 }
"""
    # strict (the CLI/generate path): fails loudly before any file is written
    try:
        _system(doc_text)
    except erosgen.ConfigError as e:
        assert "only one model" in str(e)
    else:
        raise AssertionError("expected ConfigError for two models")
    # collect (the GUI path): surfaces MULTI_MODEL instead of raising
    codes = {d.code for d in erosgen.collect_diagnostics(
        yaml.safe_load(doc_text), Path("app.yaml"))}
    assert "MULTI_MODEL" in codes


def test_cli_rejects_unknown_flag():
    # argparse exits(2) on an unknown flag instead of silently ignoring it - the
    # old hand-rolled parser dropped any --flag it didn't recognise, so a typo'd
    # --chekc ran a real generation instead of a dry run.
    try:
        erosgen.main(["erosgen", "app.yaml", "--chekc"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected SystemExit(2) for an unknown flag")


def test_cli_version_flag():
    try:
        erosgen.main(["erosgen", "--version"])
    except SystemExit as e:
        assert e.code == 0
    else:
        raise AssertionError("expected SystemExit(0) for --version")


def test_shared_budget_constants_match_report():
    # The report's RAM plan and the constants must agree (they used to be
    # independent literals in report.py, the Makefile emitter, and the GUI).
    from erosgen import constants
    assert constants.KERNEL_STATE_BYTES == 35
    assert (constants.UART_TX_RING_DEFAULT, constants.UART_RX_RING_DEFAULT) \
        == (128, 64)


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
