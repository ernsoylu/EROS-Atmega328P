/**
 * @file    timer0_pwm.h
 * @brief   Timer0 fast-PWM driver - two extra 8-bit PWM channels.
 *
 * Pins: OC0A = PD6/D6, OC0B = PD5/D5. Fast PWM mode 3 (TOP = 0xFF),
 * prescaler /64: f = 16 MHz / (64 * 256) = 976.6 Hz, duty 0..255.
 *
 * This complements the 16-bit Timer1 driver (reference-demo/pwm.c,
 * 1 kHz, permille resolution). Timer2 would offer two more channels
 * but is the EROS kernel tick and MUST NOT be touched by drivers.
 *
 * Conflicts: PD6/AIN0 and PD5 are lost as GPIOs; PD6 is also the
 * analog comparator's positive input (acomp.c with ACOMP_IN_AIN0).
 * The root demo uses PD5 for the TASK_REPORT heartbeat.
 *
 * Duty 0 quirk: as with Timer1, OCR = BOTTOM still emits a one-cycle
 * spike, so 0 disconnects the pin from the waveform generator and
 * drives it low - a true 0%. (255 is a true 100% in hardware.)
 *
 * No ISR is used; WCET of every call is a few cycles.
 */

#ifndef TIMER0_PWM_H
#define TIMER0_PWM_H

#include <stdint.h>

#define T0PWM_CH_A 0u /* OC0A / PD6 / D6 */
#define T0PWM_CH_B 1u /* OC0B / PD5 / D5 */

/** Configure Timer0 fast PWM @ 976.6 Hz, both channels duty 0 (pins
 *  low). Call with interrupts disabled (e.g. from StartupHook()). */
void T0PWM_Init(void);

/** Set duty 0..255 on T0PWM_CH_A or T0PWM_CH_B. */
void T0PWM_SetDuty(uint8_t channel, uint8_t duty);

/** Last commanded duty of the channel. */
uint8_t T0PWM_GetDuty(uint8_t channel);

#endif /* TIMER0_PWM_H */
