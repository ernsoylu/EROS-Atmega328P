/**
 * @file    acomp.c
 * @brief   Analog comparator driver implementation (see acomp.h).
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/atomic.h>

#include "acomp.h"

static volatile uint8_t acompEvents;

void ACOMP_Init(uint8_t positiveInput, uint8_t sense)
{
    const uint8_t bandgap =
        (positiveInput == ACOMP_IN_BANDGAP) ? (uint8_t)(1u << ACBG) : 0u;

    /* Configure sense first with the interrupt disabled, clear the
     * flag a mode change may have set, then enable. */
    ACSR = (uint8_t)(bandgap | ((sense & 0x03u) << ACIS0));
    ACSR |= (uint8_t)(1u << ACI);  /* write 1 clears the flag          */
    ACSR |= (uint8_t)(1u << ACIE);
}

uint8_t ACOMP_Read(void)
{
    return ((ACSR & (uint8_t)(1u << ACO)) != 0u) ? 1u : 0u;
}

uint8_t ACOMP_FetchEvents(void)
{
    uint8_t count;

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        count = acompEvents;
        acompEvents = 0u;
    }
    return count;
}

ISR(ANALOG_COMP_vect) /* Category 1: no OS service calls */
{
    if (acompEvents < 255u)
    {
        acompEvents++;
    }
}
