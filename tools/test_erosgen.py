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


def test_synthesizes_init_task_when_no_autostart():
    """An app with alarms but no autostart task gets a synthesized TASK_INIT so
    OS_StartAlarms() is actually called. Without it nothing arms the alarms and
    the scheduler idles forever - the sample_app dead-firmware bug."""
    from erosgen.emit import emit_config_c, emit_main_skeleton
    text = BASE + """
tasks:
  - { name: worker, period_ms: 10, wcet_ms: 1 }
resources: [{ name: r, users: [worker] }]
"""
    doc = yaml.safe_load(text)
    s = _system(text)
    # a synthesized autostart init task now exists at priority 0
    assert s.synthesized_init == "INIT"
    init = next(t for t in s.tasks if t.name == "INIT")
    assert init.autostart and init.priority == 0 and init.entry == "Task_Init"
    # the generated main.c arms the alarms from that task
    main_c = emit_main_skeleton(s)
    assert "void Task_Init(void)" in main_c
    assert "OS_StartAlarms();" in main_c
    # config.c lists it as autostart
    assert "[TASK_INIT] = { Task_Init, 1u" in emit_config_c(s)
    # collect mode surfaces it as a non-error info diagnostic (for the GUI)
    diags = erosgen.collect_diagnostics(doc, Path("app.yaml"))
    assert [d for d in diags if d.severity == "error"] == []
    assert any(d.code == "SYNTH_INIT" and d.severity == "info" for d in diags)


def test_declared_autostart_suppresses_synthesis():
    """A hand-authored autostart task is the OS_StartAlarms site; nothing is
    synthesized and the existing output is unchanged."""
    text = BASE + """
tasks:
  - { name: boot, autostart: true, wcet_ms: 1 }
  - { name: worker, period_ms: 10, wcet_ms: 1 }
resources: [{ name: r, users: [worker] }]
"""
    s = _system(text)
    assert s.synthesized_init is None
    assert not any(t.name == "INIT" for t in s.tasks)


def test_mcu_profile_loads_atmega328p():
    from erosgen.mcu import load_profile
    p = load_profile("atmega328p")
    assert p.name == "atmega328p"
    assert p.known_peripherals["uart"] == "uart.c"
    assert p.peripheral_pins["spi"] == ["PB2", "PB3", "PB4", "PB5"]
    assert p.driver_init["adc"] == "Adc_Init();"
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
                              emit_rte_cfg_h, emit_rte_h, emit_rte_swc_h)
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
    got[f"Rte_{rm.name}.h"] = emit_rte_swc_h(rm, "app.yaml")   # contract header
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
    # a uint16 output bound to pwm emits a Pwm_SetDutyCycle adapter, no #error
    port = BoundPort(Signal("OUT_Duty_Pm", "uint16_T", "out"),
                     "out", "pwm", {}, "Duty_Pm")
    rm = ResolvedModel("motor", "motor_initialize", "motor_Runnable", 20,
                       [], [port], None)
    c = emit_rte_c(rm, "app.yaml", integrated=True)
    assert "#error" not in c
    assert "Pwm_SetDutyCycle(permille)" in c
    assert '#include "pwm.h"' in c and "Pwm_Init();" in c
    assert "RTE_CFG_DUTY_PM_SIGNAL" in emit_rte_cfg_h(rm, "app.yaml")


def test_rte_timer0_pwm_output_binding():
    """An output port can bind to Timer0 PWM (a second 8-bit PWM family): the
    RTE emits T0Pwm_SetDuty(channel, duty) + T0Pwm_Init, no #error."""
    from erosgen.bind import DRIVERS
    from erosgen.emit import emit_rte_c, emit_rte_cfg_h
    from erosgen.models import BoundPort, ResolvedModel
    from erosgen.parse import Signal
    assert DRIVERS["timer0_pwm"].vmax == 255           # 8-bit duty
    assert DRIVERS["timer0_pwm"].required == ("channel",)
    port = BoundPort(Signal("OUT_Led_Duty", "uint8_T", "out"),
                     "out", "timer0_pwm", {"channel": 1}, "Led_Duty")
    rm = ResolvedModel("panel", "panel_initialize", "panel_Runnable", 20,
                       [], [port], None)
    c = emit_rte_c(rm, "app.yaml", integrated=True)
    assert "#error" not in c
    assert "T0Pwm_SetDuty(RTE_CFG_LED_DUTY_T0PWM_CH, duty)" in c
    assert '#include "timer0_pwm.h"' in c and "T0Pwm_Init();" in c
    cfg = emit_rte_cfg_h(rm, "app.yaml")
    assert "RTE_CFG_LED_DUTY_T0PWM_CH" in cfg and cfg.rstrip().endswith("*/")


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
    assert "uint16_t raw = Adc_ReadChannel(RTE_CFG_KNBVAL_Z_ADC_CH);" in c
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
    assert "Pwm_SetDutyCycle(permille);" in c and "#error" not in c


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
    # Only the regenerated interface files (overwrite=True) are pinned to the
    # emitter; <name>.c is the once-only, hand-authored runnable body, so it is
    # intentionally NOT compared against the skeleton emitter.
    for suffix, emit_fn, overwrite in ASW_FILES:
        if overwrite:
            got[f"{rm.name}{suffix}"] = emit_fn(rm, "app.yaml")
    for name, text in got.items():
        assert text == (d / name).read_text(), f"asw_task/{name} drifted"
    # the hand task became a real OS task + alarm, RTE-bodied
    assert "TASK_KNOB" in got["config.h"] and "ALARM_KNOB" in got["config.h"]
    assert "void Task_knob(void)" in got["Rte.c"]
    # the committed knob.c carries a real runnable (LED = knob >= threshold),
    # not the empty skeleton - and regenerating must never clobber it.
    assert "OUT_Led = (uint8_t)(IN_KnbVal >= Knb_Thresh);" in \
        (d / "knob.c").read_text()


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
        # Regeneration preserves user code by USER CODE region id (Phase 5).
        # Append a marker-wrapped block (append-only keeps taint analysis happy,
        # unlike read_text()+concat+write_text). Its id has no home in the fresh
        # skeleton, so it must survive in the ORPHAN #if 0 block, never clobbered.
        body = Path(tmp) / "ctrl.c"
        with body.open("a") as fh:
            fh.write("\n/* USER CODE BEGIN MINE */\n/* my algorithm */\n"
                     "/* USER CODE END MINE */\n")
        erosgen.main(["erosgen", str(app)])
        after = body.read_text()
        assert "/* my algorithm */" in after            # never lost
        assert "#if 0" in after and "ORPHANED" in after  # preserved as an orphan


def test_makefile_unset_drivers_dir_is_clean_error():
    # A port bound to a driver needs system.drivers_dir; if it's unset the
    # Makefile emitter must raise a clear ConfigError, not crash with a None in
    # VPATH (the TypeError a fresh GUI project used to hit on Generate).
    from erosgen.emit import emit_makefile
    from erosgen.errors import ConfigError
    s = _system(
        "system: { name: p, mcu: atmega328p, kernel_dir: ../kernel }\n"
        "tasks:\n"
        "  - { name: init, autostart: true, wcet_ms: 1 }\n"
        "  - name: t\n"
        "    period_ms: 10\n"
        "    wcet_ms: 1\n"
        "    ports:\n"
        "      in: [{ signal: IN_X, type: uint16_T, driver: adc, channel: 0 }]\n"
        "resources: [{ name: r, users: [t] }]\n")
    try:
        emit_makefile(s, Path("."))
    except ConfigError as e:
        assert "drivers_dir" in str(e) and "adc.c" in str(e)
    else:
        raise AssertionError("expected ConfigError when drivers_dir is unset")


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


# --- ASW<->ASW internal signals (one SWC's output feeds another's input) ------

def _swc(name, ins=None, outs=None):
    ports = {}
    if ins:
        ports["in"] = ins
    if outs:
        ports["out"] = outs
    return {"name": name, "period_ms": 10, "wcet_ms": 1, "ports": ports}


_A1_OUT = {"signal": "OUT_A1_B", "type": "boolean_T", "driver": "internal"}


def test_asw_asw_connection_rte():
    from erosgen.asw import resolve_asw_task
    from erosgen.diagnostics import Diagnostics
    from erosgen.emit.rte import emit_rte_c
    from erosgen.models import resolve_connections
    a1 = _swc("App1", outs=[_A1_OUT])
    a2 = _swc("App2",
              ins=[{"signal": "IN_A2_B", "type": "boolean_T",
                    "source": "App1.OUT_A1_B"}],
              outs=[{"signal": "OUT_Led_B", "type": "boolean_T", "driver": "dio",
                     "port": "B", "bit": 5}])
    sink = Diagnostics(strict=False)
    rms = [resolve_asw_task(a1, sink), resolve_asw_task(a2, sink)]
    resolve_connections(rms, {"APP1": 5, "APP2": 4}, sink)     # App1 before App2
    assert [d for d in sink.items if d.severity == "error"] == []
    assert rms[1].inputs[0].source_signal == "OUT_A1_B"
    rte = emit_rte_c(rms, "app.yaml", integrated=True)
    assert "RTE_CFG_A2_B_SIGNAL = OUT_A1_B;" in rte    # internal copy in Rte_Run
    assert "Rte_Read_A2_B" not in rte                  # no adapter for internal in
    assert "Rte_Write_A1_B" not in rte                 # no adapter for internal out


def test_asw_asw_connection_diagnostics():
    from erosgen.asw import resolve_asw_task
    from erosgen.diagnostics import Diagnostics
    from erosgen.models import resolve_connections
    a1 = _swc("App1", outs=[_A1_OUT])
    # a source pointing at a signal App1 doesn't export -> error
    bad = _swc("App2", ins=[{"signal": "IN_X_B", "type": "boolean_T",
                             "source": "App1.NOPE"}])
    sink = Diagnostics(strict=False)
    resolve_connections([resolve_asw_task(a1, sink), resolve_asw_task(bad, sink)],
                        {}, sink)
    assert "CONN_UNKNOWN_SIGNAL" in {d.code for d in sink.items}
    # producer NOT scheduled before consumer -> ordering warning
    good = _swc("App2", ins=[{"signal": "IN_A2_B", "type": "boolean_T",
                              "source": "App1.OUT_A1_B"}])
    s2 = Diagnostics(strict=False)
    resolve_connections([resolve_asw_task(a1, s2), resolve_asw_task(good, s2)],
                        {"APP1": 3, "APP2": 4}, s2)     # App1 lower priority
    assert "CONN_ORDER" in {d.code for d in s2.items}


# --- Configurable PWM frequency (Timer1) ---------------------------------

def test_pwm_timers_are_mcu_specific():
    from erosgen.mcu.profile import load_profile
    from erosgen.pwmcfg import pwm_timer
    p328, p2560 = load_profile("atmega328p"), load_profile("atmega2560")
    assert set(p328.timers) == {"timer0", "timer1", "timer2"}      # 328P: 3
    assert set(p2560.timers) == {"timer0", "timer1", "timer2",     # 2560: 6
                                 "timer3", "timer4", "timer5"}
    assert p328.timers["timer2"].get("tick")                       # tick reserved
    assert pwm_timer(p328)[0] == "timer1"                          # pwm -> Timer1


def test_pwm_frequency_defines():
    from erosgen.emit import periph_defines
    on = _system("system: { name: t, mcu: atmega328p }\n"
                 "tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]\n"
                 "resources: [{ name: r, users: [a] }]\n"
                 "peripherals: { pwm: { freq_hz: 2000 } }\n")
    assert "-DPWM_TOP=7999u" in periph_defines(on)   # 16 MHz /1 /(7999+1) = 2 kHz
    assert "-DPWM_CS=1u" in periph_defines(on)
    # activating pwm with no freq_hz emits NO -DPWM (driver keeps its 1 kHz),
    # so the reference demo stays byte-identical
    off = _system("system: { name: t, mcu: atmega328p }\n"
                  "tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]\n"
                  "resources: [{ name: r, users: [a] }]\n"
                  "peripherals: { pwm: {} }\n")
    assert not [d for d in periph_defines(off) if "PWM" in d]


def test_pwm_frequency_diagnostics():
    def codes(freq):
        doc = {"system": {"name": "t", "mcu": "atmega328p"},
               "tasks": [{"name": "a", "period_ms": 10, "wcet_ms": 1}],
               "resources": [{"name": "r", "users": ["a"]}],
               "peripherals": {"pwm": {"freq_hz": freq}}}
        return {d.code for d in erosgen.collect_diagnostics(doc, Path("x"))}
    assert not {"PWM_FREQ_RANGE", "PWM_FREQ_ROUND"} & codes(2000)   # exact
    assert "PWM_FREQ_ROUND" in codes(3000000)                       # 3 MHz coarse
    assert "PWM_FREQ_RANGE" in codes(20000000)                      # unreachable


# --- Configurable SPI (mode + clock via the SPI_Init args) ---------------

def test_spi_config_init_args():
    from erosgen.emit import emit_os_gen_h

    def initline(spi):
        s = _system(
            "system: { name: t, mcu: atmega328p, drivers_dir: ../drivers }\n"
            "tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]\n"
            "resources: [{ name: r, users: [a] }]\n"
            f"peripherals: {{ spi: {spi} }}\n")
        return next(ln.strip() for ln in emit_os_gen_h(s).splitlines()
                    if "Spi_Init" in ln)
    assert initline("{}") == "Spi_Init(SPI_MODE0, SPI_CLK_DIV16);"      # default
    assert initline("{ mode: 2, clock: 8 }") == \
        "Spi_Init(SPI_MODE2, SPI_CLK_DIV8);"


def test_spi_config_validation():
    def codes(spi):
        doc = {"system": {"name": "t", "mcu": "atmega328p"},
               "tasks": [{"name": "a", "period_ms": 10, "wcet_ms": 1}],
               "resources": [{"name": "r", "users": ["a"]}],
               "peripherals": {"spi": spi}}
        return {d.code for d in erosgen.collect_diagnostics(doc, Path("x"))}
    assert "SPI_MODE" in codes({"mode": 5})
    assert "SPI_CLOCK" in codes({"clock": 3})
    assert not {"SPI_MODE", "SPI_CLOCK"} & codes({"mode": 1, "clock": 64})


# --- Configurable ADC / I2C / Timer0 PWM (driver -D overrides) ------------

def _defs(per):
    from erosgen.emit import periph_defines
    s = _system("system: { name: t, mcu: atmega328p }\n"
                "tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]\n"
                "resources: [{ name: r, users: [a] }]\n"
                f"peripherals: {per}\n")
    return set(periph_defines(s))


def test_adc_i2c_timer0_defines():
    d = _defs("{ adc: { reference: internal, prescaler: 64 } }")
    assert "-DADC_REF=ADC_REF_1V1" in d and "-DADC_PRESCALER=6u" in d
    assert "-DI2C_TWBR=12u" in _defs("{ i2c: { speed_hz: 400000 } }")  # fast mode
    assert "-DT0PWM_CS=2u" in _defs("{ timer0_pwm: { freq_hz: 7812 } }")  # /8
    # activated but unconfigured -> no -D (driver defaults, byte-identical)
    bare = _defs("{ adc: {}, i2c: {}, timer0_pwm: {} }")
    assert not [x for x in bare if any(k in x for k in ("ADC", "I2C", "T0PWM"))]


def test_adc_i2c_timer0_validation():
    def codes(per):
        doc = {"system": {"name": "t", "mcu": "atmega328p"},
               "tasks": [{"name": "a", "period_ms": 10, "wcet_ms": 1}],
               "resources": [{"name": "r", "users": ["a"]}],
               "peripherals": per}
        return {d.code for d in erosgen.collect_diagnostics(doc, Path("x"))}
    assert "ADC_REF" in codes({"adc": {"reference": "bogus"}})
    assert "ADC_PRESCALER" in codes({"adc": {"prescaler": 5}})     # not a pow2
    assert "I2C_SPEED" in codes({"i2c": {"speed_hz": 5}})           # TWBR > 255
    assert "T0PWM_FREQ_ROUND" in codes({"timer0_pwm": {"freq_hz": 5000}})


def test_skeletons_carry_user_code_markers():
    """The once-file skeletons emit paired USER CODE markers with stable,
    element-derived ids (Phase 5) so regeneration can merge, not freeze."""
    s = _system_from(GENMAIN / "app.yaml")
    main_c = erosgen.emit_main_skeleton(s)
    assert "/* USER CODE BEGIN INCLUDES */" in main_c
    assert "/* USER CODE BEGIN STARTUP_HOOK */" in main_c
    assert "/* USER CODE BEGIN TASK_INIT_BODY */" in main_c
    asw = erosgen.emit_asw_skeleton(s, s.periodic[0])
    assert "/* USER CODE BEGIN TASK_CTRL_BODY */" in asw
    assert "/* USER CODE END TASK_CTRL_BODY */" in asw


def test_merge_reinjects_user_regions_and_refreshes_scaffold():
    """merge() carries the bytes inside a region across a scaffold change and
    drops anything a user left outside a region."""
    from erosgen.merge import begin, end, merge
    fresh = f"NEW_SIG\n{begin('B')}\n    /* seed */\n{end('B')}\ntail\n"
    edited = f"OLD_SIG\n{begin('B')}\n    do_work();\n{end('B')}\nstray\n"
    out = merge(fresh, edited)
    assert "NEW_SIG" in out and "OLD_SIG" not in out   # scaffold refreshed
    assert "do_work();" in out and "/* seed */" not in out
    assert "stray" not in out                           # out-of-region dropped
    # an untouched region (seed unchanged) round-trips byte-identically
    assert merge(fresh, fresh) == fresh


def test_merge_orphan_block_is_warned_and_preserved():
    """A region on disk with no home in the fresh skeleton is reported
    ORPHAN_USER_BLOCK and kept in a compile-safe, idempotent #if 0 block."""
    from erosgen.merge import begin, end, merge
    fresh = f"{begin('LIVE')}\n{end('LIVE')}\n"
    existing = fresh + f"{begin('GONE')}\n/* keep me */\n{end('GONE')}\n"
    sink = erosgen.Diagnostics(strict=False)
    out = merge(fresh, existing, sink, where="x.c")
    assert "/* keep me */" in out and "#if 0" in out and "#endif" in out
    assert [d.code for d in sink.items] == ["ORPHAN_USER_BLOCK"]
    # re-merging the graveyard is a fixed point (no unbounded growth)
    assert merge(fresh, out, erosgen.Diagnostics(strict=False), where="x.c") == out


def test_merge_malformed_markers_keep_file_unchanged():
    """Unbalanced markers on disk must never lose data: keep the file, warn."""
    from erosgen.merge import begin, merge
    fresh = f"{begin('A')}\n/* USER CODE END A */\n"
    broken = f"{begin('A')}\n oops, no END\n"
    sink = erosgen.Diagnostics(strict=False)
    assert merge(fresh, broken, sink, where="x.c") == broken
    assert [d.code for d in sink.items] == ["MERGE_PARSE"]


def test_write_idempotent_skip_merge_and_legacy_kept():
    """cli.write: byte-identical rewrite is skipped ('unchanged'); a once-file
    with markers merges; a legacy marker-less once-file is kept."""
    import tempfile

    from erosgen.cli import write
    from erosgen.merge import begin, end
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.h"                    # a derived artifact
        assert write(cfg, "x\n", check_only=False) == "wrote"
        assert write(cfg, "x\n", check_only=False) == "unchanged"   # skip
        assert write(cfg, "y\n", check_only=False) == "wrote"

        sk = Path(tmp) / "main.c"                        # a once-file
        v1 = f"old scaffold\n{begin('B')}\n{end('B')}\n"
        assert write(sk, v1, check_only=False, overwrite=False) == "wrote"
        sk.write_text(f"old scaffold\n{begin('B')}\n    mine();\n{end('B')}\n")
        v2 = f"new scaffold\n{begin('B')}\n{end('B')}\n"  # regen changed scaffold
        assert write(sk, v2, check_only=False, overwrite=False) == "merged"
        merged = sk.read_text()
        assert "mine();" in merged and "new scaffold" in merged  # both survive
        assert write(sk, v2, check_only=False, overwrite=False) == "unchanged"

        legacy = Path(tmp) / "legacy.c"                  # pre-markers once-file
        legacy.write_text("hand written, no markers\n")
        assert write(legacy, "regen\n", check_only=False, overwrite=False) == "kept"
        assert legacy.read_text() == "hand written, no markers\n"


def test_allowed_keys_derived_from_schema_matches_contract():
    """validate.ALLOWED_KEYS is DERIVED from schema/app.schema.json (single
    source, dep-free). This pins the contract: changing a section's key set is
    now a schema edit, and this snapshot makes the change deliberate."""
    from erosgen.validate import ALLOWED_KEYS
    assert ALLOWED_KEYS == erosgen.section_keys()      # derivation is live
    expected = {
        "doc": {"system", "sources", "peripherals", "tasks", "resources",
                "pool", "gpio", "simulink", "models", "modes"},
        "system": {"name", "mcu", "kernel_dir", "drivers_dir", "tick_hz",
                   "alarm_max_offset", "stack", "hooks", "budget"},
        "stack": {"canary", "guard_bytes", "paint_margin"},
        "hooks": {"startup", "error", "shutdown"},
        "budget": {"flash", "ram", "sram_total", "image_flash", "image_ram"},
        "task": {"name", "entry", "period_ms", "wcet_ms", "autostart",
                 "watchdog", "runnables", "runnable", "init", "ports",
                 "calibrations", "order"},
        "resource": {"name", "users", "mask_tick_isr"},
        "pool": {"block_size", "blocks"},
        "gpio": {"pin", "dir", "pullup", "name", "init"},
        "simulink": {"model", "dir", "rate_map"},
        "uart": {"baud", "tx_ring", "rx_ring"},
        "pwm": {"freq_hz"}, "spi": {"mode", "clock"},
        "adc": {"reference", "prescaler", "main_function_ms"},
        "i2c": {"speed_hz", "main_function_ms"},
        "timer0_pwm": {"freq_hz"},
        "model": {"name", "codegen_dir", "swc", "parser", "init", "runnable",
                  "rate_ms", "wcet_ms", "ports", "order", "extra_runnables"},
        "runnable_ref": {"runnable", "rate_ms", "wcet_ms"},
        "ports": {"in", "out"},
        "port": {"signal", "driver", "channel", "port", "bit", "slope",
                 "offset", "type", "description", "source"},
        "calibration": {"name", "type", "value", "description"},
        "mode": {"name", "states", "initial"},
    }
    assert ALLOWED_KEYS == expected


def test_schema_is_valid_draft2020():
    if not erosgen.schema_available():
        return                                          # [schema] extra absent
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(erosgen.load_schema())


def test_schema_accepts_every_shipped_config():
    """The schema must not reject any valid app.yaml the repo ships (else the
    --schema gate would flag good configs). model_rte is a partial RTE-emitter
    fixture (no system:) and is validated for shape too."""
    if not erosgen.schema_available():
        return
    configs = [REPO / "reference-demo" / "app.yaml"]
    configs += sorted((HERE / "fixtures").glob("*/app.yaml"))
    for c in configs:
        sink = erosgen.validate_schema(yaml.safe_load(c.read_text()))
        errs = [(d.code, d.location, d.message) for d in sink.items
                if d.severity == "error"]
        assert errs == [], f"{c.name} wrongly rejected: {errs}"


def test_schema_catches_static_violations_with_friendly_codes():
    """Static shape/value violations map to the engine's existing codes at a
    precise dotted location; unknown keys surface as SCHEMA_ADDITIONALPROPERTIES."""
    if not erosgen.schema_available():
        return

    def codes(doc):
        return {(d.code, d.location) for d in erosgen.validate_schema(doc).items}
    assert ("TICK_HZ", "system.tick_hz") in codes({"system": {"tick_hz": 500}})
    assert ("SPI_MODE", "peripherals.spi.mode") in \
        codes({"peripherals": {"spi": {"mode": 9}}})
    assert ("ADC_REF", "peripherals.adc.reference") in \
        codes({"peripherals": {"adc": {"reference": "bogus"}}})
    assert ("UART_RING", "peripherals.uart.tx_ring") in \
        codes({"peripherals": {"uart": {"tx_ring": 100}}})
    assert ("POOL_BLOCKS", "pool.blocks") in codes({"pool": {"blocks": 9}})
    assert ("SCHEMA_ADDITIONALPROPERTIES", "tasks.0") in \
        codes({"tasks": [{"name": "a", "periods_ms": 10}]})   # typo'd key


def test_schema_cli_flag_gates_generation():
    """`erosgen --schema` fails (rc 1) on a schema violation without generating,
    and passes a valid app through."""
    if not erosgen.schema_available():
        return
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "app.yaml"
        bad.write_text("system: { name: b, kernel_dir: ../kernel, tick_hz: 500 }\n"
                       "sources: [main.c]\n"
                       "tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]\n"
                       "resources: [{ name: r, users: [a] }]\n")
        assert erosgen.main(["erosgen", str(bad), "--schema"]) == 1
        assert not (Path(tmp) / "config.h").exists()     # aborted before write
    ok = REPO / "reference-demo" / "app.yaml"
    assert erosgen.main(["erosgen", str(ok), "--schema", "--check"]) == 0


def test_main_function_wired_to_matching_rate_task():
    """peripherals.<p>.main_function_ms wires <Mod>_MainFunction into the
    matching-rate ASW task's regenerated scaffold + includes its header."""
    from erosgen.emit import emit_asw_skeleton
    s = _system(BASE.replace("kernel_dir: ../kernel",
                             "kernel_dir: ../kernel, drivers_dir: ../drivers") + """
peripherals: { adc: { main_function_ms: 10 } }
tasks: [{ name: ctrl, period_ms: 10, wcet_ms: 1 }]
resources: [{ name: r, users: [ctrl] }]
""")
    assert s.main_functions == [("adc", "Adc_MainFunction", 10)]
    ctrl = next(t for t in s.periodic if t.name == "CTRL")
    body = emit_asw_skeleton(s, ctrl)
    assert '#include "adc.h"' in body
    assert "Adc_MainFunction();" in body
    # the call is scaffold (before the USER CODE body marker), so it regenerates
    assert body.index("Adc_MainFunction();") < body.index("USER CODE BEGIN TASK_CTRL_BODY")


def test_main_function_validation():
    def codes(periph, tasks):
        doc = {"system": {"name": "t", "mcu": "atmega328p",
                          "drivers_dir": "../drivers"},
               "peripherals": periph, "tasks": tasks,
               "resources": [{"name": "r", "users": [tasks[0]["name"]]}]}
        return {d.code for d in erosgen.collect_diagnostics(doc, Path("x"))}
    # a driver with no MainFunction can't be scheduled
    assert "MAIN_FUNCTION_UNSUPPORTED" in codes(
        {"spi": {"main_function_ms": 10}},
        [{"name": "a", "period_ms": 10, "wcet_ms": 1}])
    # no periodic task at the requested rate
    assert "MAIN_FUNCTION_NO_TASK" in codes(
        {"adc": {"main_function_ms": 50}},
        [{"name": "a", "period_ms": 10, "wcet_ms": 1}])
    # the happy path is clean
    assert "MAIN_FUNCTION_UNSUPPORTED" not in codes(
        {"adc": {"main_function_ms": 10}},
        [{"name": "a", "period_ms": 10, "wcet_ms": 1}])


def test_rte_contract_header_per_swc():
    """A per-SWC Rte_<SWC>.h declares Rte_Run_<SWC> + a port summary so the SWC
    compiles standalone (AUTOSAR contract phase); the combined Rte.h is
    unchanged."""
    from erosgen import emit_rte_swc_h
    from erosgen.models import BoundPort, ResolvedModel
    from erosgen.parse import Signal
    inp = BoundPort(Signal("IN_Knb_Z", "uint16_T", "in"), "in", "adc",
                    {"channel": 0}, "Knb_Z")
    out = BoundPort(Signal("OUT_Led_B", "boolean_T", "out"), "out", "dio",
                    {"port": "B", "bit": 5}, "Led_B")
    rm = ResolvedModel("appKnbSwt", "appKnbSwt_initialize", "appKnbSwt_Runnable",
                       10, [inp], [out], None)
    h = emit_rte_swc_h(rm, "app.yaml")
    assert "#ifndef RTE_APPKNBSWT_H" in h
    assert "void Rte_Run_appKnbSwt(void);" in h
    assert "IN_Knb_Z (uint16_T) <- adc" in h
    assert "OUT_Led_B (boolean_T) -> dio" in h
    # the fixtures ship one per SWC (byte-pinned by the end-to-end goldens)
    assert (HERE / "fixtures" / "model_multi" / "Rte_motor.h").exists()


def test_asw_asw_connection_cross_rate():
    """An ASW->ASW signal works across DIFFERENT rates: on the non-preemptive
    kernel the RTE's latched-global copy IS the rate-transition layer (no queue).
    With the faster producer at higher priority (runs first) there's no
    CONN_ORDER warning."""
    from erosgen.asw import resolve_asw_task
    from erosgen.diagnostics import Diagnostics
    from erosgen.emit.rte import emit_rte_c
    from erosgen.models import resolve_connections
    fast = {"name": "Sensor", "period_ms": 10, "wcet_ms": 1,
            "ports": {"out": [{"signal": "OUT_Speed_Z", "type": "uint16_T",
                               "driver": "internal"}]}}
    slow = {"name": "Logger", "period_ms": 100, "wcet_ms": 1,
            "ports": {"in": [{"signal": "IN_Speed_Z", "type": "uint16_T",
                              "source": "Sensor.OUT_Speed_Z"}]}}
    sink = Diagnostics(strict=False)
    rms = [resolve_asw_task(fast, sink), resolve_asw_task(slow, sink)]
    resolve_connections(rms, {"SENSOR": 5, "LOGGER": 3}, sink)   # producer first
    assert [d for d in sink.items if d.severity == "error"] == []
    assert not any(d.code == "CONN_ORDER" for d in sink.items)
    rte = emit_rte_c(rms, "app.yaml", integrated=True)
    assert "RTE_CFG_SPEED_Z_SIGNAL = OUT_Speed_Z;" in rte        # latched copy


def test_mode_management_emit_and_validate():
    """A modes: group generates a typed enum + Rte_Mode_/Rte_Switch_ accessors;
    validation rejects empty states, an initial not in states, and dup names."""
    from erosgen.emit import emit_modes_c, emit_modes_h
    modes = [{"name": "OpMode", "states": ["Startup", "Run", "Shutdown"],
              "initial": "Startup"}]
    h = emit_modes_h(modes, "app.yaml")
    assert "typedef enum" in h and "} Rte_ModeType_OpMode;" in h
    assert "RTE_MODE_OPMODE_RUN" in h
    assert "Rte_ModeType_OpMode Rte_Mode_OpMode(void);" in h
    assert "void Rte_Switch_OpMode(Rte_ModeType_OpMode mode);" in h
    c = emit_modes_c(modes, "app.yaml")
    assert ("static Rte_ModeType_OpMode rte_mode_OpMode = "
            "RTE_MODE_OPMODE_STARTUP;") in c
    assert "return rte_mode_OpMode;" in c and "rte_mode_OpMode = mode;" in c

    def codes(m):
        doc = {"system": {"name": "t"},
               "tasks": [{"name": "a", "period_ms": 10, "wcet_ms": 1}],
               "resources": [{"name": "r", "users": ["a"]}], "modes": m}
        return {d.code for d in erosgen.collect_diagnostics(doc, Path("x"))}
    assert "MODE_NO_STATES" in codes([{"name": "M", "states": []}])
    assert "MODE_BAD_INITIAL" in codes([{"name": "M", "states": ["A"],
                                         "initial": "Z"}])
    assert "MODE_DUP_NAME" in codes([{"name": "M", "states": ["A"]},
                                     {"name": "M", "states": ["B"]}])
    assert not (codes([{"name": "M", "states": ["A", "B"], "initial": "B"}])
                & {"MODE_NO_STATES", "MODE_BAD_INITIAL", "MODE_DUP_NAME"})


def test_extra_runnables_map_to_tasks():
    """A model's extra_runnables become their own OS tasks at their own rate: the
    RTE emits a compute-only Task_<runnable> beside Task_<model>, and the System
    schedules each (the base runnable does the port I/O)."""
    from erosgen.emit import emit_rte_c
    from erosgen.models import BoundPort, ResolvedModel
    from erosgen.parse import Signal
    out = BoundPort(Signal("OUT_Led_B", "boolean_T", "out"), "out", "dio",
                    {"port": "B", "bit": 5}, "Led_B")
    rm = ResolvedModel("swc", "swc_initialize", "swc_Fast", 10, [], [out], None,
                       [("swc_Slow", 100)])
    c = emit_rte_c(rm, "app.yaml", integrated=True)
    assert "void Task_swc(void)" in c and "Rte_Run_swc();" in c     # base: I/O
    assert "void Task_swc_Slow(void)" in c                          # extra task
    assert "swc_Slow();  /* extra runnable (compute-only) */" in c
    # System-level: the extra runnable is a real scheduled, RTE-owned task
    s = _system(BASE + """
models:
  - name: swc
    codegen_dir: ../codegen/swc_ert_rtw
    rate_ms: 10
    extra_runnables:
      - { runnable: swc_Slow, rate_ms: 100 }
tasks: [{ name: init, autostart: true, wcet_ms: 1 }]
resources: [{ name: r, users: [init] }]
""")
    names = {t.name for t in s.tasks}
    assert "SWC" in names and "SWC_SLOW" in names
    assert "SWC_SLOW" in s.model_task_names           # no asw_<rate>ms.c skeleton
    slow = next(t for t in s.tasks if t.name == "SWC_SLOW")
    assert slow.period_ms == 100 and slow.entry == "Task_swc_Slow"


def test_docs_overwrite_policy_matches_generator():
    """Docs-drift guard (Phase 4): the tools/README 'Generation & overwrite
    policy' table must state each generated file with the semantics cli.write
    actually uses — 'always' for derived artifacts, 'once' for user skeletons —
    so a fetch-based reader can't be misled by a stale table."""
    import re
    truth = {"config.h": "always", "config.c": "always", "Makefile": "always",
             "os_gen.h": "always", "main.c": "once", "asw_<rate>ms.c": "once"}
    readme = (REPO / "tools" / "README.md").read_text()
    rows = {m.group(1): m.group(2)
            for m in re.finditer(r"\|\s*`([^`]+)`\s*\|\s*(always|once)", readme)}
    assert "config.h" in rows and "main.c" in rows, \
        "tools/README overwrite table missing or renamed"
    for f, sem in truth.items():
        if f in rows:
            assert rows[f] == sem, \
                f"tools/README: {f} documented '{rows[f]}', generator is '{sem}'"


def test_docs_peripheral_names_are_real():
    """Docs-drift guard: every peripheral named in the tools/README app.yaml
    example must be a real known peripheral (subset of the MCU profile), so the
    doc can't advertise one the tool doesn't have."""
    import re

    from erosgen.mcu.profile import load_profile
    known = set(load_profile("atmega328p").known_peripherals)
    readme = (REPO / "tools" / "README.md").read_text()
    m = re.search(r"\nperipherals:.*\n((?:\s{2,}\w+:.*\n)+)", readme)
    assert m, "tools/README has no peripherals: example block"
    names = re.findall(r"^\s{2,}(\w+):", m.group(1), re.M)
    unknown = [n for n in names if n not in known]
    assert not unknown, f"tools/README names peripherals not in the profile: {unknown}"


def test_backend_protocol():
    """The AVR backend implements the Backend protocol and is what the emitters
    read via for_profile(); its idioms match the free functions byte-for-byte, so
    the refactor to an interface changes no generated output."""
    from erosgen.backends import (AVR, AvrBackend, Backend, bit_set,
                                  dio_direction_init, for_profile)
    from erosgen.mcu.profile import load_profile
    be = for_profile(load_profile("atmega328p"))
    assert isinstance(be, AvrBackend) and be.name == "avr"
    assert isinstance(be, Backend)                    # structural conformance
    assert be.bit_set("DDRB", "PB5") == bit_set("DDRB", "PB5")
    assert be.dio_direction_init("X", True) == dio_direction_init("X", True)
    assert for_profile() is AVR                       # AVR is the default


def test_parser_tier_b_swc_yaml():
    """Tier B: a model can supply a hand-authored swc.yaml (interface) instead of
    a codegen_dir; resolve_models builds the same ModelInterface + RTE."""
    import tempfile
    from pathlib import Path

    from erosgen import Diagnostics, resolve_models
    from erosgen.emit import emit_rte_c
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "knob_swc.yaml").write_text(
            "name: knob\ninit: knob_initialize\nrunnables: [knob_Runnable]\n"
            "ports:\n  in:  [{ signal: IN_Knb, type: uint16_T }]\n"
            "  out: [{ signal: OUT_Led, type: boolean_T }]\n"
            "calibrations: [{ name: Thresh, type: uint8_T }]\n")
        doc = {"models": [{
            "name": "knob", "swc": "knob_swc.yaml", "rate_ms": 10,
            "runnable": "knob_Runnable",
            "ports": {"in": [{"signal": "IN_Knb", "driver": "adc", "channel": 0}],
                      "out": [{"signal": "OUT_Led", "driver": "dio",
                               "port": "B", "bit": 5}]}}]}
        sink = Diagnostics(strict=False)
        rms = resolve_models(doc, Path(tmp), sink)
        assert [d.message for d in sink.items if d.severity == "error"] == []
        rm = rms[0]
        assert rm.init_fn == "knob_initialize" and rm.runnable_fn == "knob_Runnable"
        assert {p.signal.name for p in rm.inputs} == {"IN_Knb"}
        c = emit_rte_c(rm, "app.yaml", integrated=True)
        assert "Adc_ReadChannel" in c and "void Rte_Run_knob(void)" in c


def test_parser_tier_a_pycparser_matches_regex():
    """Tier A: the pycparser fallback (parser: c) reads the same signals/entry
    points as the regex parser on the reference ERT headers."""
    from erosgen.parse.cparse import available, parse_model_c
    if not available():
        return                                  # [parse] extra absent
    from erosgen.parse.ert import parse_model
    d = REPO / "codegen" / "appKnbSwt_ert_rtw"
    a, b = parse_model(d, "appKnbSwt"), parse_model_c(d, "appKnbSwt")
    assert {(s.name, s.ctype, s.direction) for s in a.signals} == \
        {(s.name, s.ctype, s.direction) for s in b.signals}
    assert a.runnable_fns == b.runnable_fns and a.init_fn == b.init_fn


def test_project_compile_commands_matches_makefile():
    """--project: build_plan resolves the same sources the Makefile compiles, and
    compile_commands.json emits one avr-gcc entry per TU with the real flags."""
    import json
    import re
    from erosgen.emit.project import build_plan, emit_compile_commands
    ad = HERE / "fixtures" / "model_app"
    s = _system_from(ad / "app.yaml")
    plan = build_plan(s, ad)
    appsrcs = re.search(r"APP_SRCS := (.+)", (ad / "Makefile").read_text())
    assert plan["app_srcs"] == appsrcs.group(1).split()   # guard vs Makefile
    db = json.loads(emit_compile_commands(s, ad))
    files = {e["file"] for e in db}
    assert "Rte.c" in files and "config.c" in files
    assert any("eros.c" in f for f in files)
    assert any("appKnbSwt.c" in f for f in files)         # model .c globbed in
    for e in db:
        assert e["command"].startswith("avr-gcc ")
        assert "-mmcu=atmega328p" in e["command"]
        assert e["command"].endswith(f"-c {e['file']}")


def test_project_cmakelists_and_vscode():
    from erosgen.emit.project import (emit_cmakelists,
                                      emit_vscode_cpp_properties,
                                      emit_vscode_tasks)
    ad = HERE / "fixtures" / "model_app"
    s = _system_from(ad / "app.yaml")
    cm = emit_cmakelists(s, ad)
    assert "project(knbdemo C)" in cm
    assert "add_executable(knbdemo.elf" in cm
    assert "-mmcu=${MCU}" in cm and "set(CMAKE_C_COMPILER avr-gcc)" in cm
    import json
    tasks = json.loads(emit_vscode_tasks())
    assert {t["label"] for t in tasks["tasks"]} >= {"build", "flash"}
    cpp = json.loads(emit_vscode_cpp_properties())
    assert cpp["configurations"][0]["compileCommands"].endswith(
        "compile_commands.json")


def test_project_a2l_measurements_and_characteristics():
    from erosgen import Diagnostics
    from erosgen.emit import emit_a2l
    from erosgen.models import resolve_models
    ad = HERE / "fixtures" / "model_app"
    doc = yaml.safe_load((ad / "app.yaml").read_text())
    rms = resolve_models(doc, ad, Diagnostics(strict=True))
    a2l = emit_a2l(rms, "knbdemo", "app.yaml")
    assert "ASAP2_VERSION 1 71" in a2l
    assert "/begin PROJECT EROS_knbdemo" in a2l
    assert "/begin MEASUREMENT IN_KnbVal_Z" in a2l and "UWORD" in a2l
    assert "/begin CHARACTERISTIC Knb_Thresh_Pc_Pt" in a2l


def test_workspace_deep_merge_and_detect():
    from erosgen.workspace import deep_merge, is_workspace
    base = {"system": {"name": "a", "hooks": {"error": False}}, "tasks": [1, 2]}
    over = {"system": {"hooks": {"error": True}}, "tasks": [3]}
    m = deep_merge(base, over)
    assert m["system"]["name"] == "a"                 # key left untouched
    assert m["system"]["hooks"]["error"] is True      # nested scalar overlaid
    assert m["tasks"] == [3]                          # list replaced wholesale
    assert is_workspace({"apps": ["x/app.yaml"]})
    assert not is_workspace({"system": {}})           # a plain app.yaml is not one


def test_workspace_load_and_variant():
    import tempfile
    import textwrap
    from erosgen.workspace import load_workspace
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for sub in ("nano", "uno"):
            (root / sub).mkdir()
            (root / sub / "app.yaml").write_text(f"system: {{ name: {sub} }}\n")
        wf = root / "erosproject.yaml"
        wf.write_text(textwrap.dedent("""
            name: prod
            variants:
              release: { system: { budget: { flash: 3072 } } }
            apps:
              - nano/app.yaml
              - uno/app.yaml
        """))
        name, apps = load_workspace(wf, "release")
        assert name == "prod" and len(apps) == 2
        assert {p.parent.name for p, _ in apps} == {"nano", "uno"}
        for _, doc in apps:
            assert doc["system"]["budget"]["flash"] == 3072   # overlay applied
        # no variant => apps carry their own doc unchanged
        _, plain = load_workspace(wf)
        assert all("budget" not in doc["system"] for _, doc in plain)
        # an unknown variant is a hard error, not a silent no-op
        try:
            load_workspace(wf, "nope")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "nope" in str(e)


def test_workspace_end_to_end_generates_each_app():
    """A workspace routes through _generate once per app. --check writes nothing,
    so this exercises the wiring against a real, fully-valid app.yaml."""
    import tempfile
    demo = REPO / "reference-demo" / "app.yaml"
    with tempfile.TemporaryDirectory() as d:
        wf = Path(d) / "erosproject.yaml"
        wf.write_text(f"name: ws\napps:\n  - {demo}\n  - {demo}\n")  # abs paths
        assert erosgen.main(["erosgen", str(wf), "--check"]) == 0


def test_atmega32u4_profile_tick_retarget():
    from erosgen.emit.makefile import tick_timer_def
    from erosgen.mcu import load_profile
    u4 = load_profile("atmega32u4")
    assert u4.ports == "BCDEF"
    assert "timer2" not in u4.timers                  # the reason for the retarget
    assert u4.timers["timer3"].get("tick") is True    # tick moves to Timer3
    assert u4.avrdude_part == "m32u4"
    # the tick define retargets only the 32U4; 328P/2560 keep Timer2 (no -D, so
    # their golden Makefiles - and the compiled kernel - stay byte-identical)
    assert tick_timer_def(u4) == " -DEROS_TICK_TIMER=3"
    assert tick_timer_def(load_profile("atmega328p")) == ""
    assert tick_timer_def(load_profile("atmega2560")) == ""


def test_atmega328p_makefile_has_no_tick_define():
    """Guard: the Timer2 default must never leak -DEROS_TICK_TIMER into a 328P
    build, or every 328P golden Makefile would drift."""
    mk = (HERE / "fixtures" / "model_app" / "Makefile").read_text()
    assert "EROS_TICK_TIMER" not in mk


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
