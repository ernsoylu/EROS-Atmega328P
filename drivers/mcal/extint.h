/**
 * @file    extint.h
 * @brief   External (INT0/INT1) and pin-change (PCINT) interrupt driver.
 *
 * Pins: INT0 = PD2/D2, INT1 = PD3/D3. Pin-change banks: bank 0 = PORTB
 * (PCINT0..7), bank 1 = PORTC (PCINT8..14), bank 2 = PORTD
 * (PCINT16..23) - any I/O pin can be a change source.
 *
 * Event model - counters, not callbacks: the ISRs only increment an
 * 8-bit event counter (and, for PCINT, latch the current port level);
 * tasks poll with the Fetch functions (atomic read-and-clear). This
 * keeps every ISR OSEK **Category 1** (no OS service calls), a few
 * cycles long, and free of the "user callback running in ISR context"
 * trap. If an event must RELEASE a task, poll the counter from an
 * existing high-rate task - at a 10 ms rate that adds at most 10 ms
 * latency, which is the same order as the tick anyway.
 *
 * PCINT caveat: a pin-change interrupt fires for BOTH edges of ANY
 * enabled pin in the bank and carries no pin identity; the fetched
 * level snapshot is taken in the ISR, but edges arriving faster than
 * the polling rate can only be counted, not attributed. Use INT0/1
 * when edge/pin identity matters.
 *
 * These are also the wake sources that work from deeper sleep modes
 * (INT0/1 level, any PCINT) - relevant only if the kernel's idle
 * policy ever goes below IDLE.
 */

#ifndef EXTINT_H
#define EXTINT_H

#include <stdint.h>

/* INT0/INT1 sense (EICRA encoding): */
#define EXTINT_SENSE_LOW     0u /* low level (also the deep-sleep wake) */
#define EXTINT_SENSE_CHANGE  1u
#define EXTINT_SENSE_FALLING 2u
#define EXTINT_SENSE_RISING  3u

/** Enable INT0 (which = 0) or INT1 (which = 1) with the given sense.
 *  Pin direction/pull-up is the application's job. Counting starts
 *  immediately. Call with interrupts disabled or from task level. */
void ExtInt_Enable(uint8_t which, uint8_t sense);

/** Disable INT0/INT1. */
void ExtInt_Disable(uint8_t which);

/** Atomically fetch and clear the event count (saturates at 255). */
uint8_t ExtInt_FetchCount(uint8_t which);

/** Enable pin-change interrupts for `mask` pins of bank 0/1/2
 *  (PCMSKn bit positions = PCINT number modulo 8). A bank that does
 *  not exist (bank > 2, or banks 1/2 on the ATmega32U4) is a no-op. */
void PcInt_Enable(uint8_t bank, uint8_t mask);

/** Disable the given pins; the bank is switched off when empty.
 *  Nonexistent banks are a no-op (see PcInt_Enable). */
void PcInt_Disable(uint8_t bank, uint8_t mask);

/** Atomically fetch and clear the bank's event count (saturates at
 *  255); *level receives the port input snapshot (PINx) taken at the
 *  most recent event. An invalid bank (> 2) yields count 0, level 0. */
uint8_t PcInt_FetchCount(uint8_t bank, uint8_t *level);

#endif /* EXTINT_H */
