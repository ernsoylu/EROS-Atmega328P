/**
 * @file    icp.h
 * @brief   Timer1 input-capture driver - period / pulse-width
 *          measurement on ICP1.
 *
 * Pin: ICP1 = PB0/D8. Timer1 free-runs (normal mode) at /64 ->
 * 4 us per tick; the capture unit timestamps input edges in hardware
 * (zero software jitter), noise canceler on (4-cycle glitch filter).
 *
 * Measures, per signal cycle: period between rising edges and the
 * high-time (pulse width) - i.e. frequency and duty of the input:
 *   f  = 250000 / periodTicks  [Hz]     (ticks are 4 us)
 *   dc = 1000 * pulseTicks / periodTicks  [permille]
 * Range: period 8 us .. 262 ms (approx. 4 Hz .. tens of kHz; above
 * ~10 kHz the 2-ISR-per-cycle load becomes the practical ceiling -
 * ~55 cycles per ISR means ~7% CPU at 10 kHz). A signal slower than
 * ~4 Hz (or removed) is reported as absent via the return value.
 *
 * **EXCLUSIVE with the Timer1 PWM driver** (reference-demo/pwm.c):
 * both own Timer1 - PWM uses ICR1 as TOP, this driver uses ICR1 as the
 * capture register. Link/initialise one or the other, never both.
 * (Timer2 is the kernel tick and is not an option.)
 *
 * ISRs (capture + overflow) are OSEK Category 1: they only timestamp
 * and set flags - tasks poll ICP_Get().
 */

#ifndef ICP_H
#define ICP_H

#include <stdint.h>

#define ICP_TICK_US   4u       /* one timer tick at 16 MHz / 64        */
#define ICP_TICK_HZ   250000uL /* timer ticks per second               */

/** Start Timer1 in normal mode /64 with capture on ICP1/PB0 (input).
 *  Call with interrupts disabled (e.g. from StartupHook()). */
void ICP_Init(void);

/** Fetch the most recent complete measurement (atomic).
 *  @param periodTicks rising-to-rising distance in 4 us ticks
 *  @param pulseTicks  high time in 4 us ticks
 *  @return 1 = values valid and signal alive, 0 = no signal (none yet,
 *          removed, or slower than the 262 ms range). */
uint8_t ICP_Get(uint16_t *periodTicks, uint16_t *pulseTicks);

#endif /* ICP_H */
