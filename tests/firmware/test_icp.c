/**
 * @file    test_icp.c
 * @brief   Test of the Timer1 input-capture driver under simavr.
 *
 * The host drives ICP1 (PB0) with a pulse train (see FLAGS_icp): rising
 * edges ~900 us apart with a ~400 us high time. The driver timestamps the
 * edges in hardware and reports period/pulse in 4 us ticks. Assertions use
 * wide tolerance bands so simavr's timer granularity does not matter.
 *
 * NOTE: depends on simavr modelling Timer1 input capture on the ICP1 pin;
 * kept in the stimulus matrix (continue-on-error) until validated in CI.
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/delay.h>
#include "icp.h"
#include "testkit.h"

int main(void)
{
    uint16_t period = 0u, pulse = 0u;
    uint8_t  alive;

    tk_init();

    DDRB &= (uint8_t)~(1u << PB0);   /* ICP1 input */

    ICP_Init();
    sei();

    _delay_ms(6);                    /* let the host pulse train play out */

    alive = ICP_Get(&period, &pulse);
    TK_ASSERT(alive == 1u, "signal-alive");

    /* ~900 us period at 4 us/tick = ~225 ticks. */
    TK_ASSERT(period > 150u && period < 300u, "period-band");

    /* ~400 us high time = ~100 ticks, and less than the period. */
    TK_ASSERT(pulse > 40u && pulse < period, "pulse-band");

    tk_pass();
}
