/**
 * @file    timer0_pwm.c
 * @brief   Timer0 fast-PWM driver implementation (see timer0_pwm.h).
 */

#include <avr/io.h>

#include "timer0_pwm.h"

static uint8_t t0Duty[2];

void T0PWM_Init(void)
{
    DDRD  |= (uint8_t)((1u << PD6) | (1u << PD5));
    PORTD &= (uint8_t)~((1u << PD6) | (1u << PD5));

    /* Mode 3 (fast PWM, TOP = 0xFF), both channels disconnected =
     * true 0% at boot; prescaler /64 -> 976.6 Hz. */
    TCCR0A = (uint8_t)((1u << WGM01) | (1u << WGM00));
    TCCR0B = (uint8_t)((1u << CS01) | (1u << CS00));
    OCR0A  = 0u;
    OCR0B  = 0u;
    t0Duty[0] = 0u;
    t0Duty[1] = 0u;
}

void T0PWM_SetDuty(uint8_t channel, uint8_t duty)
{
    if (channel == T0PWM_CH_A)
    {
        t0Duty[0] = duty;
        if (duty == 0u)
        {
            /* OCR = BOTTOM still spikes one cycle per period: true 0%
             * needs the pin disconnected and driven low (same quirk
             * and remedy as the Timer1 driver). */
            TCCR0A &= (uint8_t)~(1u << COM0A1);
            PORTD  &= (uint8_t)~(1u << PD6);
        }
        else
        {
            OCR0A   = duty;
            TCCR0A |= (uint8_t)(1u << COM0A1);
        }
    }
    else
    {
        t0Duty[1] = duty;
        if (duty == 0u)
        {
            TCCR0A &= (uint8_t)~(1u << COM0B1);
            PORTD  &= (uint8_t)~(1u << PD5);
        }
        else
        {
            OCR0B   = duty;
            TCCR0A |= (uint8_t)(1u << COM0B1);
        }
    }
}

uint8_t T0PWM_GetDuty(uint8_t channel)
{
    return t0Duty[(channel == T0PWM_CH_A) ? 0u : 1u];
}
