/**
 * @file    extint.c
 * @brief   External / pin-change interrupt driver implementation
 *          (see extint.h). All ISRs are OSEK Category 1.
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/atomic.h>

#include "extint.h"

/* Saturating 8-bit event counters, ISR-written -> volatile. */
static volatile uint8_t extIntCount[2];
static volatile uint8_t pcIntCount[3];
static volatile uint8_t pcIntLevel[3]; /* PINx snapshot at last event */

static void CountSaturating(volatile uint8_t *counter)
{
    if (*counter < 255u)
    {
        (*counter)++;
    }
}

/* ------------------------------------------------------------------ */
/* INT0 / INT1                                                         */
/* ------------------------------------------------------------------ */

void ExtInt_Enable(uint8_t which, uint8_t sense)
{
    const uint8_t shift = (which != 0u) ? 2u : 0u; /* ISC1x : ISC0x */
    const uint8_t bit   = (which != 0u) ? (uint8_t)(1u << INT1)
                                        : (uint8_t)(1u << INT0);

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        EICRA = (uint8_t)((EICRA & (uint8_t)~(0x03u << shift)) |
                          ((sense & 0x03u) << shift));
        EIFR  = bit; /* discard a stale pending edge */
        EIMSK |= bit;
    }
}

void ExtInt_Disable(uint8_t which)
{
    EIMSK &= (which != 0u) ? (uint8_t)~(1u << INT1)
                           : (uint8_t)~(1u << INT0);
}

uint8_t ExtInt_FetchCount(uint8_t which)
{
    const uint8_t idx = (which != 0u) ? 1u : 0u;
    uint8_t count;

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        count = extIntCount[idx];
        extIntCount[idx] = 0u;
    }
    return count;
}

ISR(INT0_vect) /* Category 1: no OS service calls */
{
    CountSaturating(&extIntCount[0]);
}

ISR(INT1_vect) /* Category 1: no OS service calls */
{
    CountSaturating(&extIntCount[1]);
}

/* ------------------------------------------------------------------ */
/* PCINT banks: 0 = PORTB, 1 = PORTC, 2 = PORTD                        */
/* ------------------------------------------------------------------ */

/** PCMSK0..2 are consecutive only in the register map, not in avr-libc
 *  headers - use an explicit lookup. */
static volatile uint8_t *PcMsk(uint8_t bank)
{
    volatile uint8_t *reg = &PCMSK0;

    if (bank == 1u)
    {
        reg = &PCMSK1;
    }
    else if (bank == 2u)
    {
        reg = &PCMSK2;
    }
    else
    {
        /* bank 0 */
    }
    return reg;
}

void PcInt_Enable(uint8_t bank, uint8_t mask)
{
    const uint8_t bit = (uint8_t)(1u << (bank & 0x03u));

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        *PcMsk(bank) |= mask;
        PCIFR  = bit; /* discard a stale pending change */
        PCICR |= bit;
    }
}

void PcInt_Disable(uint8_t bank, uint8_t mask)
{
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        *PcMsk(bank) &= (uint8_t)~mask;
        if (*PcMsk(bank) == 0u)
        {
            PCICR &= (uint8_t)~(1u << (bank & 0x03u));
        }
    }
}

uint8_t PcInt_FetchCount(uint8_t bank, uint8_t *level)
{
    const uint8_t idx = bank % 3u;
    uint8_t count;

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        count = pcIntCount[idx];
        pcIntCount[idx] = 0u;
        *level = pcIntLevel[idx];
    }
    return count;
}

ISR(PCINT0_vect) /* Category 1: no OS service calls */
{
    pcIntLevel[0] = PINB;
    CountSaturating(&pcIntCount[0]);
}

ISR(PCINT1_vect) /* Category 1: no OS service calls */
{
    pcIntLevel[1] = PINC;
    CountSaturating(&pcIntCount[1]);
}

ISR(PCINT2_vect) /* Category 1: no OS service calls */
{
    pcIntLevel[2] = PIND;
    CountSaturating(&pcIntCount[2]);
}
