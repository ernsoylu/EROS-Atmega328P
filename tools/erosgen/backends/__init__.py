"""Code-generation backend: the CPU-family idioms the emitters render.

AVR is the only family today; its idioms are re-exported here as the default so
emitters stay backend-agnostic (they import from ``..backends``, not
``..backends.avr``). A second family - e.g. ESP32 - would add its own module and
be selected here by MCU family. This is the code-gen counterpart to mcu/profile,
which holds per-*variant* data (328P vs 2560) rather than per-*family* idioms.
"""
from .avr import bit_clear, bit_read, bit_set, dio_direction_init

__all__ = ["bit_set", "bit_clear", "bit_read", "dio_direction_init"]
