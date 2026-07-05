/**
 * @file    acomp.h
 * @brief   Analog comparator driver - sub-microsecond analog events
 *          without ADC latency.
 *
 * Inputs: positive = AIN0/PD6/D6 or the internal 1.1 V bandgap;
 * negative = AIN1/PD7/D7. Output ACO flips in ~us; events are counted
 * in a Category-1 ISR and polled by tasks (same counter model as
 * extint.h - no callbacks in ISR context).
 *
 * Conflicts: PD6 (AIN0 mode) is also OC0A of the Timer0 PWM driver;
 * PD7 is lost as a GPIO. The ADC multiplexer is NOT borrowed (ACME
 * stays off), so the ADC driver is unaffected.
 *
 * No blocking anywhere; WCET of every call is a few cycles.
 */

#ifndef ACOMP_H
#define ACOMP_H

#include <stdint.h>

/* Positive input selection: */
#define ACOMP_IN_AIN0    0u /* external, PD6/D6                        */
#define ACOMP_IN_BANDGAP 1u /* internal 1.1 V reference                */

/* Event sense (ACIS encoding): */
#define ACOMP_EVT_TOGGLE  0u
#define ACOMP_EVT_FALLING 2u
#define ACOMP_EVT_RISING  3u

/** Enable the comparator (positive input vs AIN1/PD7) and start
 *  counting `sense` events. Call with interrupts disabled
 *  (e.g. from StartupHook()). */
void ACOMP_Init(uint8_t positiveInput, uint8_t sense);

/** Instantaneous comparator output: 1 = V+ above V-. */
uint8_t ACOMP_Read(void);

/** Atomically fetch and clear the event count (saturates at 255). */
uint8_t ACOMP_FetchEvents(void);

#endif /* ACOMP_H */
