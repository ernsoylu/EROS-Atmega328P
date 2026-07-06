"""Type/range compatibility between ASW ports (Simulink signals) and BSW drivers.

The "smart" binding layer: given a parsed Signal (rtw type) and a models: port
binding (driver + params), verify direction and value-range compatibility, and
expose the driver facts emit/rte.py needs. Problems are reported through the
Diagnostics sink so they join the same problem list as every other check.
"""
from dataclasses import dataclass

from .parse.ert import RTW_TYPES


@dataclass(frozen=True)
class DriverSpec:
    name: str
    directions: tuple    # which port directions it can serve: ("in",) etc.
    value_ctype: str     # C type at the driver boundary
    vmax: int            # max value the driver reads/writes (min is 0 here)
    header: str          # BSW header (empty = raw avr/io.h registers, e.g. dio)
    required: tuple      # binding keys the port must provide
    init: str            # fixed BSW init call ("" = per-binding, e.g. dio DDR)
    boolean: bool = False  # actuator/sensor is on/off (no range truncation)


# The BSW drivers a model port can bind to. adc = analog in (10-bit),
# dio = digital in/out (1 bit), pwm = 8-bit duty out. Extend as drivers gain
# RTE bindings; ranges mirror drivers/ and mcu/atmega328p.yaml.
DRIVERS = {
    "adc": DriverSpec("adc", ("in",), "uint16_t", 1023, "adc.h",
                      ("channel",), "Adc_Init();"),
    "dio": DriverSpec("dio", ("in", "out"), "uint8_t", 1, "",
                      ("port", "bit"), "", boolean=True),
    # Timer1 PWM on the fixed OC1A pin; duty is permille (0..1000), so uint16 -
    # no channel/pin binding param needed (unlike adc/dio). Matches pwm.h's
    # Pwm_SetDutyCycle(uint16_t).
    "pwm": DriverSpec("pwm", ("out",), "uint16_t", 1000, "pwm.h",
                      (), "Pwm_Init();"),
    # Timer0 8-bit PWM: two channels (0 = OC0B/PD5, 1 = OC0A/PD6), duty 0..255.
    # Matches timer0_pwm.h's T0Pwm_SetDuty(channel, duty); the port picks a
    # `channel` - a second PWM family alongside the Timer1 pwm above.
    "timer0_pwm": DriverSpec("timer0_pwm", ("out",), "uint8_t", 255,
                             "timer0_pwm.h", ("channel",), "T0Pwm_Init();"),
}


def _capacity(ctype):
    """Max non-negative value the rtw type can hold, or None if unknown."""
    info = RTW_TYPES.get(ctype)
    if info is None:
        return None
    _cstd, width, signed, _is_bool = info
    return (1 << (width - (1 if signed else 0))) - 1


def check_binding(signal, direction, driver_name, params, sink, where,
                  scaled=False):
    """Validate one port binding against its parsed Signal. Reports via `sink`
    and returns the DriverSpec (or None if the driver is unknown). `scaled`
    suppresses the raw-range truncation warning: a calibrated port converts the
    value, so the signal type no longer has to fit the driver's raw range."""
    drv = DRIVERS.get(driver_name)
    if drv is None:
        sink.error("UNKNOWN_DRIVER",
                   f"{where}: unknown driver '{driver_name}' "
                   f"(known: {', '.join(sorted(DRIVERS))})", where)
        return None
    if direction not in drv.directions:
        sink.error("DRIVER_DIRECTION",
                   f"{where}: driver '{driver_name}' cannot serve an "
                   f"'{direction}' port (serves: {', '.join(drv.directions)})",
                   where)
    if signal.direction in ("in", "out") and signal.direction != direction:
        sink.error("PORT_DIRECTION_MISMATCH",
                   f"{where}: signal '{signal.name}' is {signal.direction} "
                   f"(by IN_/OUT_ prefix) but bound as an '{direction}' port",
                   where)
    for k in drv.required:
        if k not in params:
            sink.error("BINDING_MISSING_KEY",
                       f"{where}: driver '{driver_name}' needs '{k}'", where)

    cap = _capacity(signal.ctype)
    if cap is None:
        sink.error("UNKNOWN_SIGNAL_TYPE",
                   f"{where}: unknown signal type '{signal.ctype}' for "
                   f"'{signal.name}'", where)
    elif direction == "in" and drv.vmax > cap:
        # driver -> signal: the port must be able to hold the driver's range.
        sink.error("TYPE_TOO_NARROW",
                   f"{where}: {signal.ctype} (max {cap}) cannot hold driver "
                   f"'{driver_name}' range 0..{drv.vmax}", where)
    elif direction == "out" and not drv.boolean and not scaled and cap > drv.vmax:
        # signal -> valued actuator (pwm): wider signal truncates (unless a
        # calibration converts it - then the raw range no longer has to fit).
        sink.warning("RANGE_TRUNCATION",
                     f"{where}: {signal.ctype} (max {cap}) exceeds driver "
                     f"'{driver_name}' range 0..{drv.vmax}; value is truncated",
                     where)
    return drv
