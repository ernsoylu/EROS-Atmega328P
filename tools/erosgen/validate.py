"""YAML-shape validation and pin-name normalization helpers.

check_keys() is the highest-value guard (rejects typo'd keys with a "did you
mean" hint). normalize_pin() canonicalizes Arduino Nano aliases to AVR pin
names. is_pow2() backs the UART ring-size check.
"""
import difflib

from .errors import fail

# Recognised keys per YAML section - anything else is a typo and is
# rejected (with a "did you mean" hint). This is the highest-value
# guard: a misspelled 'period_ms' would otherwise silently make a task
# aperiodic with no error.
ALLOWED_KEYS = {
    "doc":        {"system", "sources", "peripherals", "tasks",
                   "resources", "pool", "gpio", "simulink"},
    "system":     {"name", "kernel_dir", "drivers_dir", "tick_hz",
                   "alarm_max_offset", "stack", "hooks", "budget"},
    "stack":      {"canary", "guard_bytes", "paint_margin"},
    "hooks":      {"startup", "error", "shutdown"},
    "budget":     {"flash", "ram", "sram_total",
                   "image_flash", "image_ram"},
    "task":       {"name", "entry", "period_ms", "wcet_ms", "autostart",
                   "watchdog", "runnables"},
    "resource":   {"name", "users", "mask_tick_isr"},
    "pool":       {"block_size", "blocks"},
    "gpio":       {"pin", "dir", "pullup", "name", "init"},
    "simulink":   {"model", "dir", "rate_map"},
    "uart":       {"baud", "tx_ring", "rx_ring"},
}


def check_keys(d, section, where):
    """Reject unknown keys in a YAML mapping, suggesting the closest
    valid key. `section` selects the allowed-key set; `where` names the
    location for the error message."""
    if not isinstance(d, dict):
        fail(f"{where}: expected a mapping, got {type(d).__name__}")
    allowed = ALLOWED_KEYS[section]
    for k in d:
        if k not in allowed:
            hint = difflib.get_close_matches(str(k), allowed, n=1)
            suffix = f" (did you mean '{hint[0]}'?)" if hint else \
                     f" (valid: {', '.join(sorted(allowed))})"
            fail(f"{where}: unknown key '{k}'{suffix}")


def is_pow2(n):
    return n >= 1 and (n & (n - 1)) == 0


# Arduino Nano silk -> ATmega328P port pin. Accept either form in YAML.
NANO_ALIASES = {}
for _d, _pp in {0: "PD0", 1: "PD1", 2: "PD2", 3: "PD3", 4: "PD4",
                5: "PD5", 6: "PD6", 7: "PD7", 8: "PB0", 9: "PB1",
                10: "PB2", 11: "PB3", 12: "PB4", 13: "PB5"}.items():
    NANO_ALIASES[f"D{_d}"] = _pp
for _a in range(6):  # A0..A5 double as PC0..PC5; A6/A7 are ADC-only
    NANO_ALIASES[f"A{_a}"] = f"PC{_a}"


def normalize_pin(name):
    """Return a canonical PORTxBIT pin name (e.g. 'PB5') from either an
    AVR name or an Arduino Nano alias ('D13', 'A4')."""
    p = str(name).upper()
    if p in NANO_ALIASES:
        return NANO_ALIASES[p]
    if (len(p) == 3 and p[0] == "P" and p[1] in "BCD" and p[2].isdigit()):
        return p
    fail(f"unknown pin '{name}' (use PB0..PD7 or Nano D0..D13 / A0..A5)")
