/* ====================================================================== *
 * eros_tick.h - system-tick timer selection (kernel-internal).
 *
 * The 1 kHz OS tick lives on Timer2 CTC for the ATmega328P / ATmega2560.
 * The ATmega32U4 has no Timer2, so a profile retargets the tick to Timer3
 * (16-bit, CTC) by compiling with -DEROS_TICK_TIMER=3 (erosgen emits this
 * from the profile's `tick:`-flagged timer). The prescaler math is identical
 * on both - F_CPU/64 / 250 = 1 kHz at 16 MHz - only the register / vector /
 * bit names differ, aliased below so eros.c has one code path.
 *
 * The default (EROS_TICK_TIMER == 2) expands to the exact Timer2 register
 * writes eros.c used before this header existed, so the 328P / 2560 object
 * code is byte-identical.
 *
 * Included only by eros.c (needs <avr/io.h> for the register symbols).
 * ====================================================================== */
#ifndef EROS_TICK_H
#define EROS_TICK_H

#ifndef EROS_TICK_TIMER
#define EROS_TICK_TIMER 2   /* Timer2: ATmega328P / ATmega2560 default */
#endif

#if EROS_TICK_TIMER == 2
/* Timer2 (8-bit). CTC = WGM21 in TCCR2A; /64 = CS22 alone (Timer2 table).
 * Timer2 has the async status register ASSR (cleared for synchronous clock). */
#define EROS_TICK_VECT      TIMER2_COMPA_vect
#define EROS_TICK_TCCRA     TCCR2A
#define EROS_TICK_TCCRB     TCCR2B
#define EROS_TICK_OCRA      OCR2A
#define EROS_TICK_TCNT      TCNT2
#define EROS_TICK_TIMSK     TIMSK2
#define EROS_TICK_TIFR      TIFR2
#define EROS_TICK_OCIE      OCIE2A
#define EROS_TICK_OCF       OCF2A
#define EROS_TICK_TCCRA_VAL ((uint8_t)(1u << WGM21))   /* CTC, OC2x disconnected */
#define EROS_TICK_TCCRB_VAL ((uint8_t)(1u << CS22))    /* prescaler /64          */
#define EROS_TICK_HAS_ASSR  1

#elif EROS_TICK_TIMER == 3
/* Timer3 (16-bit). CTC = mode 4 (WGM32 in TCCR3B); /64 = CS31|CS30 (standard
 * table). No async status register. OCR3A/TCNT3 are 16-bit; a <=255 write is
 * fine. Used for the ATmega32U4, whose Timer0 is left for timer0_pwm. */
#define EROS_TICK_VECT      TIMER3_COMPA_vect
#define EROS_TICK_TCCRA     TCCR3A
#define EROS_TICK_TCCRB     TCCR3B
#define EROS_TICK_OCRA      OCR3A
#define EROS_TICK_TCNT      TCNT3
#define EROS_TICK_TIMSK     TIMSK3
#define EROS_TICK_TIFR      TIFR3
#define EROS_TICK_OCIE      OCIE3A
#define EROS_TICK_OCF       OCF3A
#define EROS_TICK_TCCRA_VAL ((uint8_t)0u)
#define EROS_TICK_TCCRB_VAL ((uint8_t)((1u << WGM32) | (1u << CS31) | (1u << CS30)))
#define EROS_TICK_HAS_ASSR  0

#else
#error "EROS_TICK_TIMER must be 2 (Timer2) or 3 (Timer3)"
#endif

#endif /* EROS_TICK_H */
