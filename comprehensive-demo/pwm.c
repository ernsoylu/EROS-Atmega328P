/**
 * @file    pwm.c
 * @brief   Timer1 fast-PWM driver implementation (see pwm.h).
 */

#include <avr/io.h>
#include <util/atomic.h>

#include "pwm.h"

#define PWM_TOP 1999u /* 16 MHz / 8 / (1999+1) = 1 kHz */

static uint16_t pwmDutyPermille;

void PWM_Init(void)
{
    DDRB  |= (uint8_t)(1u << PB1);              /* OC1A output          */
    PORTB &= (uint8_t)~(1u << PB1);
    /* Boot at true 0%: OC1A disconnected (see PWM_SetDutyPermille). */
    TCCR1A = (uint8_t)(1u << WGM11);
    TCCR1B = (uint8_t)((1u << WGM13) | (1u << WGM12) | (1u << CS11));
    ICR1   = PWM_TOP;                           /* mode 14: ICR1 = TOP  */
    OCR1A  = 0u;
    pwmDutyPermille = 0u;
}

void PWM_SetDutyPermille(uint16_t permille)
{
    uint16_t ocr;

    if (permille > 1000u)
    {
        permille = 1000u;
    }
    pwmDutyPermille = permille;

    if (permille == 0u)
    {
        /* ATmega328P fast-PWM quirk: OCR1A = BOTTOM still emits a
         * narrow spike every TOP+1 cycles, so "duty 0" would never be
         * fully off. Disconnect OC1A from the waveform generator and
         * drive the pin low instead - a true 0% output. */
        TCCR1A &= (uint8_t)~(1u << COM1A1);
        PORTB  &= (uint8_t)~(1u << PB1);
        return;
    }

    TCCR1A |= (uint8_t)(1u << COM1A1); /* (re)connect OC1A */
    ocr = (uint16_t)(((uint32_t)permille * PWM_TOP) / 1000u);

    /* 16-bit timer registers share the TEMP byte; keep the two-byte
     * write indivisible against any future ISR that might also perform
     * 16-bit timer accesses. */
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        OCR1A = ocr;
    }
}

uint16_t PWM_GetDutyPermille(void)
{
    return pwmDutyPermille;
}
