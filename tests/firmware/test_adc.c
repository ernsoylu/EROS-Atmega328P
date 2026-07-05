/**
 * @file    test_adc.c
 * @brief   Test of the ADC driver under simavr with host-injected volts.
 *
 * The host runner injects static channel voltages (see FLAGS_adc in the
 * Makefile): A0 = 2500 mV, A1 = 1100 mV, A2 = 0 mV. Assertions are
 * reference-voltage independent (ordering + rails) so they hold whatever
 * AVcc simavr models, while still proving the mux, conversion, and result
 * registers all work.
 */

#include <avr/io.h>
#include "adc.h"
#include "testkit.h"

int main(void)
{
    uint16_t a0, a1, a2;

    tk_init();

    ADC_Init();
    TK_ASSERT((ADCSRA & (1u << ADEN)) != 0u, "aden");

    a0 = ADC_Read(0u);   /* 2500 mV */
    a1 = ADC_Read(1u);   /* 1100 mV */
    a2 = ADC_Read(2u);   /*    0 mV */

    /* 10-bit results in range. */
    TK_ASSERT(a0 < 1024u && a1 < 1024u && a2 < 1024u, "range");

    /* 0 mV must read near the bottom rail. */
    TK_ASSERT(a2 < 40u, "zero-rail");

    /* Monotonic with injected voltage: 2500 > 1100 > 0. */
    TK_ASSERT(a0 > a1, "order-a0-a1");
    TK_ASSERT(a1 > a2, "order-a1-a2");

    /* Non-trivial conversion (not stuck at 0). */
    TK_ASSERT(a0 > 100u, "a0-nonzero");

    tk_pass();
}
