/**
 * @file    pwm.h
 * @brief   Timer1 fast-PWM driver.
 *
 * The ATmega328P could generate PWM on Timer0/1/2, but under EROS Timer2 is the
 * kernel tick and MUST NOT be touched by drivers, so this driver is Timer1-only:
 * mode 14 (fast PWM, ICR1 = TOP) on OC1A / PB1 (Arduino Nano pin D9).
 *
 *   f_pwm = F_CPU / (prescaler * (TOP + 1))
 *
 * Frequency is configurable: erosgen computes the prescaler + TOP for
 * `peripherals.pwm.freq_hz` and passes them as -DPWM_CS / -DPWM_TOP. With no
 * freq_hz set the defaults below give the historical 1 kHz (16 MHz / 8 / 2000).
 * Duty is permille (0..1000) to keep the API integer-only.
 */

#ifndef PWM_H
#define PWM_H

#include <stdint.h>

/** Configure Timer1 mode 14 at the compiled frequency, duty 0, OC1A/PB1 output.
 *  Call with interrupts disabled (e.g. from StartupHook()). */
void Pwm_Init(void);

/** Set duty cycle in permille; values above 1000 are clamped. */
void Pwm_SetDutyCycle(uint16_t permille);

/** Last commanded duty cycle in permille. */
uint16_t Pwm_GetDutyCycle(void);

#endif /* PWM_H */
