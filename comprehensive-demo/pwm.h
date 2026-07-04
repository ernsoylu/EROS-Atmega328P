/**
 * @file    pwm.h
 * @brief   Timer1 fast-PWM driver.
 *
 * The ATmega328P could generate PWM on Timer0/1/2, but under TinyOS
 * Timer2 is the kernel tick and MUST NOT be touched by drivers, so this
 * driver is Timer1-only: mode 14 (fast PWM, ICR1 = TOP), prescaler /8,
 * 1 kHz on OC1A / PB1 (Arduino Nano pin D9).
 *
 *   f_pwm = F_CPU / (8 * (TOP + 1)) = 16 MHz / (8 * 2000) = 1 kHz
 *
 * Duty is expressed in permille (0..1000) to keep the API integer-only.
 */

#ifndef PWM_H
#define PWM_H

#include <stdint.h>

/** Configure Timer1 mode 14 @ 1 kHz, duty 0, OC1A/PB1 as output.
 *  Call with interrupts disabled (e.g. from StartupHook()). */
void PWM_Init(void);

/** Set duty cycle in permille; values above 1000 are clamped. */
void PWM_SetDutyPermille(uint16_t permille);

/** Last commanded duty cycle in permille. */
uint16_t PWM_GetDutyPermille(void);

#endif /* PWM_H */
