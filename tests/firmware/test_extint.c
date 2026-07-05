/**
 * @file    test_extint.c
 * @brief   Test of the external-interrupt driver under simavr.
 *
 * The host drives INT0 (PD2) with falling edges (see FLAGS_extint). The
 * driver's Category-1 ISR counts them; the task polls with an atomic
 * fetch-and-clear. Verifies edge sensing, counting, and clear-on-read.
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/delay.h>
#include "extint.h"
#include "testkit.h"

int main(void)
{
    uint8_t c1;
    uint8_t c2;

    tk_init();

    /* PD2 input with pull-up so the host-driven edges are clean. */
    DDRD  &= (uint8_t)~(1u << PD2);
    PORTD |= (uint8_t)(1u << PD2);

    ExtInt_Enable(0u, EXTINT_SENSE_FALLING);
    sei();

    /* Let the host's scheduled edges (up to ~3 ms) arrive. */
    _delay_ms(6);

    c1 = ExtInt_FetchCount(0u);
    TK_ASSERT(c1 >= 1u, "counted");

    /* Fetch clears the counter. */
    c2 = ExtInt_FetchCount(0u);
    TK_ASSERT(c2 == 0u, "clear-on-read");

    tk_pass();
}
