"""PWM timer configuration: pick a prescaler + TOP for a requested frequency.

    f_pwm = F_CPU / (prescaler * (TOP + 1))

The timers available (their count, width and prescalers) are MCU-specific and
declared in the profile's `timers:` table - a 328P has three, a 2560 six. This
module is pure math shared by validation (model.py) and emit (emit/makefile.py)
so the two can never disagree about the generated -DPWM_TOP / -DPWM_CS. The
kernel-tick timer is never a PWM option.
"""


def f_cpu_hz(profile):
    """Numeric F_CPU from the profile macro string ('16000000UL' -> 16000000)."""
    digits = "".join(c for c in str(profile.f_cpu) if c.isdigit())
    return int(digits) if digits else 16000000


def pwm_timer(profile, driver="pwm"):
    """The timer the given PWM driver runs on: (name, prescalers, width) from the
    profile's timers: table, or None if the profile declares none (then the
    driver's built-in default frequency stands). The tick timer is excluded."""
    for name, spec in (profile.timers or {}).items():
        if spec.get("pwm") == driver and not spec.get("tick"):
            return (name, list(spec.get("prescalers") or [1, 8, 64, 256, 1024]),
                    int(spec.get("width", 16)))
    return None


def timer_pwm(freq_hz, f_cpu, prescalers, width=16):
    """(cs_bits, top, actual_hz) for a fast-PWM nearest `freq_hz` on a timer with
    the given ordered prescaler list and counter `width` (bits). cs_bits indexes
    the prescaler list (1-based - the AVR CSn2:0 encoding). Uses the smallest
    prescaler whose TOP fits (max duty resolution); None if unreachable."""
    if freq_hz <= 0:
        return None
    top_max = (1 << width) - 1
    for i, presc in enumerate(prescalers):
        top = round(f_cpu / (presc * freq_hz)) - 1
        if 1 <= top <= top_max:
            return i + 1, top, f_cpu / (presc * (top + 1))
    return None


def timer0_pwm_cs(freq_hz, f_cpu, prescalers):
    """(cs_bits, actual_hz) for an 8-bit Timer0 mode-3 PWM (TOP fixed at 0xFF, so
    only the prescaler sets f = F_CPU/(presc*256)); pick the closest prescaler.
    cs_bits is the 1-based index (AVR CSn encoding). None if freq_hz <= 0."""
    if freq_hz <= 0:
        return None
    best = None
    for i, presc in enumerate(prescalers):
        f = f_cpu / (presc * 256)
        if best is None or abs(f - freq_hz) < abs(best[1] - freq_hz):
            best = (i + 1, f)
    return best


# ADC prescaler -> ADCSRA ADPS2:0 value (== log2 of the divisor).
_ADC_PRESCALERS = (2, 4, 8, 16, 32, 64, 128)
_ADC_REF = {"avcc": "ADC_REF_AVCC", "internal": "ADC_REF_1V1",
            "aref": "ADC_REF_AREF"}


def adc_prescaler_bits(prescaler):
    """ADCSRA ADPS field for a /prescaler divisor, or None if not a valid one."""
    if prescaler in _ADC_PRESCALERS:
        return _ADC_PRESCALERS.index(prescaler) + 1
    return None


def adc_ref_symbol(ref):
    """ADC_REF_* driver macro for 'avcc' | 'internal' | 'aref', else None."""
    return _ADC_REF.get(str(ref).lower())


def i2c_twbr(speed_hz, f_cpu):
    """TWBR for SCL = speed_hz at prescaler 1: (F_CPU/speed - 16)/2. Valid only
    when it fits 0..255 (roughly F_CPU/(16+2*255) .. F_CPU/16); None otherwise."""
    if speed_hz <= 0:
        return None
    twbr = round((f_cpu / speed_hz - 16) / 2)
    return twbr if 0 <= twbr <= 255 else None


def pwm_config(profile, freq_hz):
    """(cs_bits, top, actual_hz) for `peripherals.pwm.freq_hz` on this MCU's PWM
    timer, or None if unreachable / no PWM timer. Convenience over pwm_timer +
    timer_pwm used by both validation and emit."""
    timer = pwm_timer(profile)
    if timer is None:
        return None
    _name, prescalers, width = timer
    return timer_pwm(freq_hz, f_cpu_hz(profile), prescalers, width)
