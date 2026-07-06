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


def test_arduino_uno_extends_atmega328p():
    # The Uno is a 328P board: it inherits the whole chip profile via `extends`
    # and overrides only the flash baud (Optiboot 115200, not the Nano's 57600).
    from erosgen.mcu import load_profile
    uno = load_profile("arduino_uno")
    assert uno.name == "arduino_uno"
    assert uno.mcu_gcc == "atmega328p"                 # same silicon -> -mmcu
    assert uno.avrdude_part == "m328p"                 # inherited
    assert uno.avrdude_programmer == "arduino"         # inherited
    assert uno.avrdude_baud == 115200                  # overridden
    # chip facts inherited unchanged from atmega328p
    assert uno.ports == "BCD"
    assert uno.aliases["D13"] == "PB5"
    assert uno.peripheral_pins["spi"] == ["PB2", "PB3", "PB4", "PB5"]
    # the base profile is untouched (still the old-bootloader default)
    assert load_profile("atmega328p").avrdude_baud == 57600


def test_arduino_uno_makefile_targets_115200():
    from erosgen import System
    from erosgen.emit import emit_makefile
    doc = {"system": {"name": "u", "mcu": "arduino_uno", "kernel_dir": "../kernel"},
           "tasks": [{"name": "a", "period_ms": 10, "wcet_ms": 1}],
           "resources": [{"name": "r", "users": ["a"]}]}
    s = System(doc, Path("app.yaml"))
    assert s.mcu == "arduino_uno" and s.profile.mcu_gcc == "atmega328p"
    mk = emit_makefile(s, Path(".").resolve())
    assert "BAUD    ?= 115200" in mk           # Uno flash baud
    assert "-p m328p -c arduino" in mk         # 328P part, arduino programmer
    assert "MCU     := atmega328p" in mk       # still compiles as a 328P


def test_profile_extends_cycle_guard():
    from erosgen.mcu.profile import _resolve
    try:
        _resolve("atmega328p", {"atmega328p"})  # already on the resolve stack
    except ValueError as e:
        assert "cycle" in str(e)
    else:
        raise AssertionError("expected an extends-cycle ValueError")


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


def _scaled_adc_model(slope, offset):
    from erosgen.models import BoundPort, ResolvedModel
    from erosgen.parse import Signal
    port = BoundPort(Signal("IN_KnbVal_Z", "uint16_T", "in"), "in", "adc",
                     {"channel": 0}, "KnbVal_Z", slope, offset)
    return ResolvedModel("knb", "knb_initialize", "knb_Runnable", 10,
                         [port], [], None)


def test_rte_scaling_integer_math():
    from erosgen.emit.rte import emit_rte_c, emit_rte_cfg_h
    rm = _scaled_adc_model(5, -3)          # both ints -> integer (int32_t) path
    cfg, c = emit_rte_cfg_h(rm, "app.yaml"), emit_rte_c(rm, "app.yaml", True)
    # slope/offset become auditable declarative #defines, not inline literals
    assert "RTE_CFG_KNBVAL_Z_SLOPE" in cfg and "RTE_CFG_KNBVAL_Z_OFFSET" in cfg
    assert "5" in cfg and "-3" in cfg
    # the read adapter returns the signal's C type and calibrates via int32_t
    assert "static uint16_t Rte_Read_KnbVal_Z(void)" in c
    assert "uint16_t raw = ADC_Read(RTE_CFG_KNBVAL_Z_ADC_CH);" in c
    assert ("(int32_t)raw * RTE_CFG_KNBVAL_Z_SLOPE + RTE_CFG_KNBVAL_Z_OFFSET"
            in c)


def test_rte_scaling_float_math():
    from erosgen.emit.rte import emit_rte_c, emit_rte_cfg_h
    rm = _scaled_adc_model(0.1, 1.5)       # a float -> single-precision path
    cfg, c = emit_rte_cfg_h(rm, "app.yaml"), emit_rte_c(rm, "app.yaml", True)
    assert "0.1f" in cfg and "1.5f" in cfg   # real32 literals, not doubles
    assert "(float)raw * RTE_CFG_KNBVAL_Z_SLOPE + RTE_CFG_KNBVAL_Z_OFFSET" in c


def _resolve_ports(ports):
    """resolve_models over the real appKnbSwt ERT dir with the given ports:
    block. Returns (resolved_models, {diagnostic codes})."""
    from erosgen import Diagnostics
    from erosgen.models import resolve_models
    doc = {"models": [{"name": "appKnbSwt", "rate_ms": 10,
                       "runnable": "appKnbSwt_Runnable",
                       "codegen_dir": str(REPO / "codegen" / "appKnbSwt_ert_rtw"),
                       "ports": ports}]}
    sink = Diagnostics(strict=False)
    rms = resolve_models(doc, Path("."), sink)
    return rms, {d.code for d in sink.items}


def test_scaling_end_to_end_input():
    rms, codes = _resolve_ports({"in": [{"signal": "IN_KnbVal_Z", "driver": "adc",
                                         "channel": 0, "slope": 2, "offset": 1}]})
    assert "SCALING_UNSUPPORTED" not in codes and "SCALING_NOT_NUMBER" not in codes
    assert rms[0].inputs[0].scaled


def test_scaling_rejected_on_boolean_driver():
    # OUT_Led1_B is a dio (boolean) port: a linear scale is meaningless -> loud.
    _, codes = _resolve_ports({"out": [{"signal": "OUT_Led1_B", "driver": "dio",
                                        "port": "B", "bit": 5, "slope": 2}]})
    assert "SCALING_UNSUPPORTED" in codes


def test_rte_scaling_output_pwm():
    from erosgen.emit.rte import emit_rte_c, emit_rte_cfg_h
    from erosgen.models import BoundPort, ResolvedModel
    from erosgen.parse import Signal
    # ASW duty in percent (0..100) -> driver permille (0..1000): slope 10.
    port = BoundPort(Signal("OUT_Duty_Pc", "uint16_T", "out"), "out", "pwm",
                     {}, "Duty_Pc", 10, 0)
    rm = ResolvedModel("motor", "motor_initialize", "motor_Runnable", 20,
                       [], [port], None)
    cfg, c = emit_rte_cfg_h(rm, "app.yaml"), emit_rte_c(rm, "app.yaml", True)
    assert "RTE_CFG_DUTY_PC_SLOPE" in cfg and "RTE_CFG_DUTY_PC_OFFSET" in cfg
    # the write adapter takes the ASW value and converts it to permille
    assert "static void Rte_Write_Duty_Pc(uint16_t value)" in c
    assert ("uint16_t permille = (uint16_t)((int32_t)value"
            " * RTE_CFG_DUTY_PC_SLOPE + RTE_CFG_DUTY_PC_OFFSET);" in c)
    assert "PWM_SetDutyPermille(permille);" in c and "#error" not in c


def test_scaling_suppresses_range_truncation():
    from erosgen import Diagnostics
    from erosgen.bind import check_binding
    from erosgen.parse import Signal
    sig = Signal("OUT_Duty_Pc", "uint16_T", "out")   # cap 65535 > pwm vmax 1000

    def codes(scaled):
        s = Diagnostics(strict=False)
        check_binding(sig, "out", "pwm", {}, s, "port", scaled=scaled)
        return {d.code for d in s.items}
    assert "RANGE_TRUNCATION" in codes(False)      # unscaled: wide signal truncates
    assert "RANGE_TRUNCATION" not in codes(True)   # scaled: the conversion handles it


def test_scaling_rejects_non_number():
    _, codes = _resolve_ports({"in": [{"signal": "IN_KnbVal_Z", "driver": "adc",
                                       "channel": 0, "slope": "fast"}]})
    assert "SCALING_NOT_NUMBER" in codes


def test_avr_backend_gpio_idioms():
    # The AVR backend owns the register/GPIO code-gen idioms os_gen + rte render;
    # pin these so a future ESP32 backend has an explicit contract to match.
    from erosgen.backends import bit_clear, bit_read, bit_set, dio_direction_init
    assert bit_set("DDRB", "PB5") == "DDRB |= (uint8_t)(1u << PB5)"
    assert bit_clear("PORTB", "PB5") == "PORTB &= (uint8_t)~(1u << PB5)"
    assert bit_read("RTE_CFG_X_PIN", "RTE_CFG_X_BIT") == \
        "(uint8_t)((RTE_CFG_X_PIN >> RTE_CFG_X_BIT) & 1u)"
    # dio direction init: DDR/PORT operators column-aligned, output driven low
    assert dio_direction_init("LED1_B", True) == [
        "RTE_CFG_LED1_B_DDR  |= (uint8_t)(1u << RTE_CFG_LED1_B_BIT);",
        "RTE_CFG_LED1_B_PORT &= (uint8_t)~(1u << RTE_CFG_LED1_B_BIT);"]
    assert dio_direction_init("SW", False) == [
        "RTE_CFG_SW_DDR  &= (uint8_t)~(1u << RTE_CFG_SW_BIT);"]


def test_multi_model_end_to_end_goldens():
    """Two SWCs in one app: two synthesized tasks/alarms and one combined RTE
    (a Task_<model> per SWC, per-model RTE_CFG_<MODEL>_* identity defines). Pins
    the whole multi-model output. Regenerate after an intended change:
        uv run python tools/erosgen.py tools/fixtures/model_multi/app.yaml"""
    from erosgen import Diagnostics, System, resolve_models
    from erosgen.emit import (emit_config_c, emit_config_h, emit_main_skeleton,
                              emit_makefile, emit_os_gen_h, emit_rte_c,
                              emit_rte_cfg_h, emit_rte_h)
    d = HERE / "fixtures" / "model_multi"
    doc = yaml.safe_load((d / "app.yaml").read_text())
    s = System(doc, d / "app.yaml")
    assert s.model_task_names == {"APPKNBSWT", "MOTOR"}   # both became OS tasks
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
    assert len(rms) == 2
    got["Rte.h"] = emit_rte_h(rms, "app.yaml")
    got["Rte_Cfg.h"] = emit_rte_cfg_h(rms, "app.yaml")
    got["Rte.c"] = emit_rte_c(rms, "app.yaml", integrated=True)
    for name, text in got.items():
        assert text == (d / name).read_text(), f"model_multi/{name} drifted"
    # per-model identity is namespaced; both SWCs get a task body + alarm
    assert "RTE_CFG_APPKNBSWT_INIT_FN" in got["Rte_Cfg.h"]
    assert "RTE_CFG_MOTOR_RUNNABLE_FN" in got["Rte_Cfg.h"]
    assert "void Task_appKnbSwt(void)" in got["Rte.c"]
    assert "void Task_motor(void)" in got["Rte.c"]
    assert "ALARM_MOTOR" in got["config.h"] and "ALARM_APPKNBSWT" in got["config.h"]


def test_multi_model_stem_collision_rejected():
    # Port #defines (RTE_CFG_<TAG>_*) share one namespace, so two SWCs binding a
    # signal with the same stem must be flagged, not silently miscompiled.
    from erosgen import Diagnostics
    from erosgen.models import resolve_models
    cg = str(REPO / "codegen" / "appKnbSwt_ert_rtw")
    entry = {"name": "appKnbSwt", "codegen_dir": cg, "rate_ms": 10,
             "runnable": "appKnbSwt_Runnable",
             "ports": {"in": [{"signal": "IN_KnbVal_Z", "driver": "adc",
                               "channel": 0}]}}
    doc = {"models": [entry, dict(entry)]}   # two SWCs, same KnbVal_Z stem
    sink = Diagnostics(strict=False)
    resolve_models(doc, Path("."), sink)
    assert "PORT_STEM_COLLISION" in {d.code for d in sink.items}


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


# --- Hand-authored ASW tasks (a task with a ports:/calibrations: interface) ---

_ASW_APP = """
system:
  name: handdemo
  kernel_dir: {kernel}
  drivers_dir: {drivers}
tasks:
  - {{ name: init, autostart: true, wcet_ms: 1 }}
  - name: ctrl
    period_ms: 100
    wcet_ms: 2
    ports:
      in:
        - {{ signal: IN_Knob, type: uint16_T, description: "knob", driver: adc, channel: 0 }}
      out:
        - {{ signal: OUT_Led, type: boolean_T, description: "LED", driver: dio, port: B, bit: 5 }}
    calibrations:
      - {{ name: Thresh, type: uint16_T, value: 512, description: "trip point" }}
resources:
  - {{ name: rte, users: [ctrl] }}
""".format(kernel=REPO / "kernel", drivers=REPO / "drivers")


def test_asw_task_resolves_like_a_model():
    from erosgen.asw import is_asw_task, resolve_asw_task
    from erosgen.diagnostics import Diagnostics
    task = yaml.safe_load(_ASW_APP)["tasks"][1]
    assert is_asw_task(task) and not is_asw_task({"name": "plain", "period_ms": 10})
    sink = Diagnostics(strict=False)
    rm = resolve_asw_task(task, sink)
    assert [d for d in sink.items if d.severity == "error"] == []
    assert rm.name == "ctrl" and rm.rate_ms == 100
    assert rm.runnable_fn == "ctrl_Runnable" and rm.init_fn == "ctrl_initialize"
    assert [p.signal.name for p in rm.inputs] == ["IN_Knob"]
    assert rm.outputs[0].driver == "dio" and rm.outputs[0].params["bit"] == 5


def test_asw_task_is_rte_bodied_in_system():
    s = _system(_ASW_APP)
    assert "CTRL" in s.asw_task_names and "CTRL" in s.rte_task_names
    ctrl = next(t for t in s.tasks if t.name == "CTRL")
    assert ctrl.entry == "Task_ctrl"          # RTE body, not asw_100ms.c
    # a hand ASW task must not also get a per-rate skeleton in the Makefile
    mk = erosgen.emit_makefile(s, Path("."))
    assert "asw_100ms.c" not in mk
    for src in ("ctrl.c", "ctrl_Intfc.c", "ctrl_Param.c", "Rte.c"):
        assert src in mk
    assert "adc.c" in mk                        # the in-port driver source


def test_asw_task_rte_and_skeletons():
    from erosgen.asw import resolve_asw_tasks
    from erosgen.diagnostics import Diagnostics
    from erosgen.emit.asw import (ASW_FILES, emit_asw_intfc_h, emit_asw_param_c,
                                  emit_asw_task_c)
    doc = yaml.safe_load(_ASW_APP)
    rm = resolve_asw_tasks(doc, Diagnostics(strict=True))[0]
    rte = erosgen.emit_rte_c([rm], "app.yaml", integrated=True)
    assert "void Task_ctrl(void)" in rte and "Rte_Run_ctrl();" in rte
    assert "RTE_CFG_KNOB_SIGNAL = Rte_Read_Knob();" in rte   # IN_Knob <- adc
    # the interface header declares the ports as extern C globals + descriptions
    intfc = emit_asw_intfc_h(rm, "app.yaml")
    assert "extern uint16_t IN_Knob;" in intfc and "/* knob */" in intfc
    # calibration storage carries the app.yaml value
    assert "uint16_t Thresh = 512;" in emit_asw_param_c(rm, "app.yaml")
    # the runnable body is the one file that is never overwritten
    assert dict((suf, ov) for suf, _fn, ov in ASW_FILES)[".c"] is False
    assert "ctrl_Runnable(void)" in emit_asw_task_c(rm, "app.yaml")


def test_asw_task_unbound_port_is_fatal():
    # A hand ASW port with no driver fails generation, exactly like a model port.
    from erosgen.asw import resolve_asw_tasks
    from erosgen.diagnostics import Diagnostics
    from erosgen.errors import ConfigError
    doc = yaml.safe_load(_ASW_APP)
    doc["tasks"][1]["ports"]["in"][0].pop("driver")
    try:
        resolve_asw_tasks(doc, Diagnostics(strict=True))
    except ConfigError:
        pass
    else:
        raise AssertionError("expected ConfigError for an unbound ASW port")


def test_asw_task_fixture_goldens():
    """The committed asw_task fixture (a hand-authored ASW task) regenerates
    byte-for-byte: config.*, Makefile, main.c, os_gen.h, Rte.*, and the six-file
    knob{,_Intfc,_Param}.{c,h} skeleton. CI additionally builds it with avr-gcc.
    Regenerate: uv run python tools/erosgen.py tools/fixtures/asw_task/app.yaml"""
    from erosgen import Diagnostics, System
    from erosgen.asw import resolve_asw_tasks
    from erosgen.emit import (emit_config_c, emit_config_h, emit_main_skeleton,
                              emit_makefile, emit_os_gen_h, emit_rte_c,
                              emit_rte_cfg_h, emit_rte_h)
    from erosgen.emit.asw import ASW_FILES
    d = HERE / "fixtures" / "asw_task"
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
    rms = resolve_asw_tasks(doc, sink)
    assert [x.message for x in sink.items if x.severity == "error"] == []
    rm = rms[0]
    got["Rte.h"] = emit_rte_h(rms, "app.yaml")
    got["Rte_Cfg.h"] = emit_rte_cfg_h(rms, "app.yaml")
    got["Rte.c"] = emit_rte_c(rms, "app.yaml", integrated=True)
    for suffix, emit_fn, _ov in ASW_FILES:
        got[f"{rm.name}{suffix}"] = emit_fn(rm, "app.yaml")
    for name, text in got.items():
        assert text == (d / name).read_text(), f"asw_task/{name} drifted"
    # the hand task became a real OS task + alarm, RTE-bodied
    assert "TASK_KNOB" in got["config.h"] and "ALARM_KNOB" in got["config.h"]
    assert "void Task_knob(void)" in got["Rte.c"]


def test_asw_task_end_to_end_generate():
    # tempfile (not pytest's tmp_path) so this file still runs standalone.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "app.yaml"
        app.write_text(_ASW_APP)
        rc = erosgen.main(["erosgen", str(app)])
        assert rc == 0
        for f in ("ctrl.c", "ctrl.h", "ctrl_Intfc.c", "ctrl_Intfc.h",
                  "ctrl_Param.c", "ctrl_Param.h", "Rte.c", "config.c",
                  "Makefile"):
            assert (Path(tmp) / f).exists(), f"missing generated {f}"
        # regenerating must NOT clobber a hand-edited runnable (overwrite=False)
        body = Path(tmp) / "ctrl.c"
        body.write_text(body.read_text() + "\n/* my algorithm */\n")
        erosgen.main(["erosgen", str(app)])
        assert "/* my algorithm */" in body.read_text()


def test_within_rate_order_tiebreak():
    # Same-rate tasks tie-break by explicit `order` (higher = more urgent), so a
    # hand task and a codegen task at one rate interleave freely.
    s = _system(
        "system: { name: t }\n"
        "tasks:\n"
        "  - { name: a, period_ms: 100, wcet_ms: 1, order: 0 }\n"
        "  - { name: b, period_ms: 100, wcet_ms: 1, order: 5 }\n"
        "resources: [{ name: r, users: [a] }]\n")
    pa = next(t for t in s.tasks if t.name == "A").priority
    pb = next(t for t in s.tasks if t.name == "B").priority
    assert pb > pa          # b has the higher order -> the higher priority


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
