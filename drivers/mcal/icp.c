/**
 * @file    icp.c
 * @brief   Timer1 input-capture driver implementation (see icp.h).
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/atomic.h>

#include "icp.h"

#define ICP_FRESH_PERIOD 0x01u
#define ICP_FRESH_PULSE  0x02u
#define ICP_FRESH_BOTH   0x03u

/* ISR-written measurement state -> volatile; 16-bit values are read
 * under ATOMIC_BLOCK in Icp_Get(). */
static volatile uint16_t icpPeriod;
static volatile uint16_t icpPulse;
static volatile uint16_t icpLastRise;
static volatile uint8_t  icpHaveRise; /* a rising edge has been captured
                                         since init / signal loss       */
static volatile uint8_t  icpFresh;
static volatile uint8_t  icpOvfCount;

void Icp_Init(void)
{
    DDRB  &= (uint8_t)~(1u << PB0); /* ICP1 input                       */

    TCCR1A = 0u;                    /* normal mode, pins disconnected   */
    /* Noise canceler, capture rising edge first, prescaler /64. */
    TCCR1B = (uint8_t)((1u << ICNC1) | (1u << ICES1) |
                       (1u << CS11) | (1u << CS10));
    TCNT1  = 0u;
    TIFR1  = (uint8_t)((1u << ICF1) | (1u << TOV1)); /* clear stale     */
    TIMSK1 = (uint8_t)((1u << ICIE1) | (1u << TOIE1));

    icpFresh    = 0u;
    icpHaveRise = 0u;
    icpOvfCount = 2u; /* "no signal" until the first full cycle */
}

ISR(TIMER1_CAPT_vect) /* Category 1: no OS service calls */
{
    const uint16_t stamp = ICR1;

    if ((TCCR1B & (uint8_t)(1u << ICES1)) != 0u)
    {
        /* Rising edge: close the previous cycle, open a new one. The
         * first rise after init or signal loss has no previous rise to
         * measure against (icpLastRise is stale), so it only anchors
         * the next cycle - no period is reported for it. */
        if (icpHaveRise != 0u)
        {
            icpPeriod = stamp - icpLastRise;
            icpFresh |= ICP_FRESH_PERIOD;
        }
        icpHaveRise = 1u;
        icpLastRise = stamp;
        icpOvfCount = 0u;
        TCCR1B &= (uint8_t)~(1u << ICES1); /* next: falling            */
    }
    else
    {
        /* Falling edge: high time since the rising edge. */
        icpPulse  = stamp - icpLastRise;
        icpFresh |= ICP_FRESH_PULSE;
        TCCR1B |= (uint8_t)(1u << ICES1);  /* next: rising             */
    }
    /* Toggling ICES1 can set a spurious capture flag (datasheet
     * 20.6.3): clear it so the ISR is not re-entered for the toggle. */
    TIFR1 = (uint8_t)(1u << ICF1);
}

ISR(TIMER1_OVF_vect) /* Category 1: no OS service calls */
{
    /* Two overflows (2 x 262 ms) without a capture = signal gone. */
    if (icpOvfCount < 2u)
    {
        icpOvfCount++;
    }
    else
    {
        icpFresh    = 0u;
        icpHaveRise = 0u; /* next rise re-anchors, no stale period */
    }
}

uint8_t Icp_Get(uint16_t *periodTicks, uint16_t *pulseTicks)
{
    uint8_t valid = 0u;

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        if ((icpFresh == ICP_FRESH_BOTH) && (icpOvfCount < 2u))
        {
            *periodTicks = icpPeriod;
            *pulseTicks  = icpPulse;
            valid = 1u;
        }
    }
    return valid;
}
