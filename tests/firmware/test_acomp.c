/**
 * @file    test_acomp.c
 * @brief   Smoke test of the analog-comparator driver under simavr.
 *
 * simavr does not model the analog comparator's analog front-end, so this
 * is a register/smoke test: init must program the comparator without
 * hanging, ACOMP_Read() must return a valid boolean, and the event fetch
 * must clear. Kept in the stimulus matrix (continue-on-error) since deeper
 * behaviour cannot be exercised without an analog model.
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include "acomp.h"
#include "testkit.h"

int main(void)
{
    uint8_t level, ev;

    tk_init();

    ACOMP_Init(ACOMP_IN_BANDGAP, ACOMP_EVT_TOGGLE);
    sei();

    /* Comparator must be enabled (ACD bit in ACSR clear = enabled). */
    TK_ASSERT((ACSR & (1u << ACD)) == 0u, "enabled");

    /* Read returns a strict boolean. */
    level = ACOMP_Read();
    TK_ASSERT(level <= 1u, "read-bool");

    /* Fetch-and-clear leaves the counter at zero. */
    (void)ACOMP_FetchEvents();
    ev = ACOMP_FetchEvents();
    TK_ASSERT(ev == 0u, "clear-on-read");

    tk_pass();
}
