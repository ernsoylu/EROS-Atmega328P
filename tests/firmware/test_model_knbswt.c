/**
 * @file    test_model_knbswt.c
 * @brief   End-to-end simavr test of the Simulink model 'appKnbSwt'
 *          integrated through appKnbSwt_glue.c.
 *
 * The host runner sweeps the knob on ADC A0 across the full 10-bit range:
 * 1023 -> 0 over 5 s, then 0 -> 1023 over 5 s (--adc-sweep 0:5000:0:5000),
 * and watches the digital output on PB5 (--watch-pin B,5).
 *
 * This firmware runs the RTE loop (Rte_Run_appKnbSwt: read A0 port ->
 * appKnbSwt_Runnable() -> write PB5 port) across that sweep and self-checks
 * the model's behaviour: the model turns the LED ON when the knob drops
 * below Knb_Thresh_Pc_Pt (20 % -> raw ~205) and OFF again only when it
 * rises past Knb_Thresh_Pc_Pt + Knb_Hyst_Pc_Pt (25 % -> raw ~256) - a
 * 5 %-point hysteresis window. A full sweep must therefore produce exactly
 * one ON edge (descending through ~205) and exactly one OFF edge
 * (ascending through ~256), and must cover both rails.
 *
 * The knob values are read as real ADC counts on-chip, so the checks are
 * independent of simavr's exact reference voltage.
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/delay.h>

#include "Rte.h"               /* Rte_Init(), Rte_Run_appKnbSwt() */
#include "appKnbSwt_Intfc.h"   /* IN_KnbVal_Z, OUT_Led1_B (self-check) */
#include "testkit.h"

/* ~10.5 s of sweep at ~5 ms per iteration. */
#define ITERS       2050u
#define STEP_MS     5u

/* Count a LED transition and latch the knob value at the first edge of
 * this direction (0xFFFF = not yet seen). */
static void record_edge(uint16_t knb, uint8_t *edges, uint16_t *first)
{
    (*edges)++;
    if (*first == 0xFFFFu)
    {
        *first = knb;
    }
}

int main(void)
{
    uint16_t min_knb  = 0xFFFFu;
    uint16_t max_knb  = 0u;
    uint16_t on_edge  = 0xFFFFu;
    uint16_t off_edge = 0xFFFFu;
    uint8_t  on_edges  = 0u;
    uint8_t  off_edges = 0u;
    uint8_t  prev = 0xFFu;                 /* 0xFF = no previous sample */

    tk_init();                             /* polled-UART report channel */
    Rte_Init();                            /* BSW init + calibration + ASW init */
    sei();

    for (uint16_t i = 0u; i < ITERS; i++)
    {
        uint16_t knb;
        uint8_t  led;

        Rte_Run_appKnbSwt();               /* read port -> runnable -> write port */
        knb = IN_KnbVal_Z;
        led = OUT_Led1_B ? 1u : 0u;

        if (knb < min_knb) min_knb = knb;
        if (knb > max_knb) max_knb = knb;

        if (prev != 0xFFu && led != prev)
        {
            if (led == 1u) record_edge(knb, &on_edges,  &on_edge);
            else           record_edge(knb, &off_edges, &off_edge);
        }
        prev = led;

        _delay_ms(STEP_MS);
    }

    /* Observable summary (visible with runtest --echo / in CI logs). */
    tk_print("knob range ");   tk_print_u16(min_knb);
    tk_print("..");            tk_print_u16(max_knb);
    tk_print("  ON@");         tk_print_u16(on_edge);
    tk_print(" OFF@");         tk_print_u16(off_edge);
    tk_print(" edges=");       tk_print_u16(on_edges);
    tk_putc('/');              tk_print_u16(off_edges);
    tk_putc('\n');

    /* The sweep really reached both rails (1023 -> 0 -> 1023). */
    TK_ASSERT(max_knb >= 1015u, "sweep-top");
    TK_ASSERT(min_knb <= 8u,    "sweep-bottom");

    /* Exactly one switch each way. */
    TK_ASSERT(on_edges  == 1u, "one-on-edge");
    TK_ASSERT(off_edges == 1u, "one-off-edge");

    /* ON at the 20 % threshold (raw ~205), OFF at 20 % + 5 % hysteresis
     * (raw ~256): the edges must land in disjoint windows around each. */
    TK_ASSERT(on_edge  >= 180u && on_edge  <= 230u, "on-threshold");
    TK_ASSERT(off_edge >= 231u && off_edge <= 281u, "off-threshold");

    tk_pass();
}
