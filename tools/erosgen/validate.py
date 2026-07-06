"""YAML-shape validation and pin-name normalization helpers.

check_keys() is the highest-value guard (rejects typo'd keys with a "did you
mean" hint). normalize_pin() canonicalizes Arduino Nano aliases to AVR pin
names. Both report through the Diagnostics sink so the same checks work in
strict (raise) and collect (accumulate) modes; message text is unchanged.
"""
import difflib

# Recognised keys per YAML section - anything else is a typo and is
# rejected (with a "did you mean" hint). This is the highest-value
# guard: a misspelled 'period_ms' would otherwise silently make a task
# aperiodic with no error.
ALLOWED_KEYS = {
    "doc":        {"system", "sources", "peripherals", "tasks",
                   "resources", "pool", "gpio", "simulink", "models"},
    "system":     {"name", "mcu", "kernel_dir", "drivers_dir", "tick_hz",
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
    # RTE generation from a Simulink SWC (see rte/README.md, models: schema).
    "model":      {"name", "codegen_dir", "init", "runnable", "rate_ms",
                   "wcet_ms", "ports"},
    "ports":      {"in", "out"},
    "port":       {"signal", "driver", "channel", "port", "bit",
                   "slope", "offset"},
}


def check_keys(d, section, where, sink):
    """Reject unknown keys in a YAML mapping, suggesting the closest valid key.
    `section` selects the allowed-key set; `where` names the location for the
    error message. Returns True if the mapping is well-shaped enough to keep
    inspecting (False after a non-mapping, so collect mode can skip it)."""
    if not isinstance(d, dict):
        sink.error("BAD_MAPPING",
                   f"{where}: expected a mapping, got {type(d).__name__}", where)
        return False
    allowed = ALLOWED_KEYS[section]
    for k in d:
        if k not in allowed:
            hint = difflib.get_close_matches(str(k), allowed, n=1)
            suffix = f" (did you mean '{hint[0]}'?)" if hint else \
                     f" (valid: {', '.join(sorted(allowed))})"
            sink.error("UNKNOWN_KEY", f"{where}: unknown key '{k}'{suffix}", where)
    return True


def is_pow2(n):
    return n >= 1 and (n & (n - 1)) == 0


def normalize_pin(name, profile, sink):
    """Return a canonical PORTxBIT pin name (e.g. 'PB5') from an AVR name or a
    board alias ('D13', 'A4'), using the MCU profile's aliases + valid ports.
    Returns None (collect mode) after an unrecognized pin; in strict mode
    sink.error raises before returning."""
    p = str(name).upper()
    if p in profile.aliases:
        return profile.aliases[p]
    if (len(p) == 3 and p[0] == "P" and p[1] in profile.ports and p[2].isdigit()):
        return p
    sink.error("UNKNOWN_PIN",
               f"unknown pin '{name}' (use a PXn on ports {profile.ports} "
               "or a board alias)", "gpio")
    return None
