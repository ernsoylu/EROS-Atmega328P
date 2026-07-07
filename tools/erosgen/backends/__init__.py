"""Code-generation backends: the CPU-family idioms the emitters render.

AVR is the only family; its idioms live in `avr.AvrBackend`, exposed here as the
`AVR` default instance. Emitters read the `Backend` interface via
`for_profile(profile)` (which returns `AVR`) rather than a concrete module, so
the family idioms have one swappable home. This is the code-gen counterpart to
`mcu/profile`, which holds per-*variant* data (328P vs 2560) rather than
per-*family* idioms. The bit helpers stay importable directly too.
"""
from .avr import (AvrBackend, bit_clear, bit_read, bit_set,
                  dio_direction_init)
from .base import Backend

AVR = AvrBackend()          # the default (and only) backend instance


def for_profile(profile=None):
    """Return the code-gen backend for an MCU profile. AVR is the only family,
    so this always returns `AVR`; it is the single point a future family would
    branch on."""
    return AVR


__all__ = ["Backend", "AvrBackend", "AVR", "for_profile",
           "bit_set", "bit_clear", "bit_read", "dio_direction_init"]
