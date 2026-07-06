"""MCU hardware profile loaded from mcu/<name>.yaml.

A profile is the complete set of target-specific facts erosgen needs: valid
ports, board pin aliases, toolchain strings (mmcu / F_CPU / avrdude), and the
peripheral/pin/driver tables. Adding a target is a new YAML file here - System
and the emitters read the selected profile, so no Python changes are needed for
a same-family part.
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

PROFILE_DIR = Path(__file__).resolve().parent


def _read_raw(name):
    path = PROFILE_DIR / f"{name}.yaml"
    if not path.exists():
        avail = ", ".join(sorted(p.stem for p in PROFILE_DIR.glob("*.yaml")))
        raise FileNotFoundError(
            f"erosgen: no MCU profile '{name}' at {path} (have: {avail})")
    return yaml.safe_load(path.read_text()) or {}


def _deep_merge(base, over):
    """Merge `over` onto `base` for profile `extends`: nested dicts merge, scalars
    and lists replace. The child profile wins on every leaf it sets."""
    out = dict(base)
    for k, v in over.items():
        out[k] = (_deep_merge(out[k], v)
                  if isinstance(v, dict) and isinstance(out.get(k), dict) else v)
    return out


def _resolve(name, seen):
    """Read mcu/<name>.yaml, applying `extends` (a base profile whose facts the
    child inherits and may override - e.g. a board that reuses a chip). Guards
    against extends cycles."""
    if name in seen:
        raise ValueError(
            f"erosgen: MCU profile 'extends' cycle involving '{name}'")
    d = _read_raw(name)
    parent = d.pop("extends", None)
    if parent:
        d = _deep_merge(_resolve(parent, seen | {name}), d)
    return d


@dataclass(frozen=True)
class MCUProfile:
    name: str
    ports: str                # valid AVR port letters, e.g. "BCD"
    aliases: dict             # board silk -> AVR pin, e.g. {"D13": "PB5"}
    mcu_gcc: str              # -mmcu value, e.g. "atmega328p"
    f_cpu: str                # F_CPU macro value, e.g. "16000000UL"
    avrdude_part: str         # avrdude -p, e.g. "m328p"
    avrdude_programmer: str   # avrdude -c, e.g. "arduino"
    avrdude_baud: int         # avrdude -b default
    avrdude_baud_note: str    # trailing comment on the BAUD line
    known_peripherals: dict   # peripheral -> driver source .c
    peripheral_pins: dict     # peripheral -> [pin, ...]
    conflicts: list           # [(a, b, reason), ...]
    driver_init: dict         # peripheral -> Init() call
    driver_header: dict       # peripheral -> header

    @classmethod
    def load(cls, name):
        d = _resolve(name, set())
        tc = d.get("toolchain", {}) or {}
        avr = tc.get("avrdude", {}) or {}
        return cls(
            name=d.get("name", name),
            ports=str(d.get("ports", "")),
            aliases=dict(d.get("aliases", {})),
            mcu_gcc=tc.get("mcu", d.get("name", name)),
            f_cpu=str(tc.get("f_cpu", "16000000UL")),
            avrdude_part=avr.get("part", ""),
            avrdude_programmer=avr.get("programmer", "arduino"),
            avrdude_baud=int(avr.get("baud", 57600)),
            avrdude_baud_note=avr.get("baud_note", ""),
            known_peripherals=dict(d.get("peripherals", {})),
            peripheral_pins={k: list(v)
                             for k, v in (d.get("peripheral_pins") or {}).items()},
            conflicts=[tuple(c) for c in (d.get("conflicts") or [])],
            driver_init=dict(d.get("driver_init", {})),
            driver_header=dict(d.get("driver_header", {})),
        )


def load_profile(name="atmega328p"):
    return MCUProfile.load(name)
