/**
 * @file    test_timer0_pwm.c
 * @brief   Pure-firmware test of the Timer0 PWM driver under simavr.
 *
 * Verifies the driver's register programming directly (mode, prescaler,
 * compare outputs, the true-0%/true-100% quirks) - all deterministic and
 * observable in memory, so no host stimulus is needed.
 */

#include <avr/io.h>
#include "timer0_pwm.h"
#include "testkit.h"

int main(void)
{
    tk_init();

    T0PWM_Init();

    /* Fast PWM mode 3: WGM01|WGM00 set, WGM02 (in TCCR0B) clear. */
    TK_ASSERT((TCCR0A & ((1u << WGM01) | (1u << WGM00))) ==
              ((1u << WGM01) | (1u << WGM00)), "wgm-mode3");
    TK_ASSERT((TCCR0B & (1u << WGM02)) == 0u, "wgm02-clear");

    /* Prescaler /64: CS01|CS00 set, CS02 clear. */
    TK_ASSERT((TCCR0B & ((1u << CS01) | (1u << CS00))) ==
              ((1u << CS01) | (1u << CS00)), "presc-64");
    TK_ASSERT((TCCR0B & (1u << CS02)) == 0u, "cs02-clear");

    /* Both channels start disconnected (true 0%) and pins driven low. */
    TK_ASSERT((TCCR0A & (1u << COM0A1)) == 0u, "init-a-disc");
    TK_ASSERT((TCCR0A & (1u << COM0B1)) == 0u, "init-b-disc");
    TK_ASSERT((DDRD & ((1u << PD6) | (1u << PD5))) ==
              ((1u << PD6) | (1u << PD5)), "pins-output");

    /* Mid duty on A connects the output and loads OCR0A. */
    T0PWM_SetDuty(T0PWM_CH_A, 128u);
    TK_ASSERT(OCR0A == 128u, "a-ocr");
    TK_ASSERT((TCCR0A & (1u << COM0A1)) != 0u, "a-connected");
    TK_ASSERT(T0PWM_GetDuty(T0PWM_CH_A) == 128u, "a-getduty");

    /* Duty 0 disconnects the pin again (true 0%). */
    T0PWM_SetDuty(T0PWM_CH_A, 0u);
    TK_ASSERT((TCCR0A & (1u << COM0A1)) == 0u, "a-zero-disc");
    TK_ASSERT(T0PWM_GetDuty(T0PWM_CH_A) == 0u, "a-zero-duty");

    /* Full duty on B. */
    T0PWM_SetDuty(T0PWM_CH_B, 255u);
    TK_ASSERT(OCR0B == 255u, "b-ocr");
    TK_ASSERT((TCCR0A & (1u << COM0B1)) != 0u, "b-connected");
    TK_ASSERT(T0PWM_GetDuty(T0PWM_CH_B) == 255u, "b-getduty");

    tk_pass();
}
