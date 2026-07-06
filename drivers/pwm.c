/**
 * @file    pwm.c
 * @brief   Timer1 fast-PWM driver implementation (see pwm.h).
 */

#include <avr/io.h>
#include <util/atomic.h>

#include "pwm.h"

/* Frequency config. erosgen overrides these with -DPWM_TOP / -DPWM_CS computed
 * from peripherals.pwm.freq_hz; the defaults reproduce the historical 1 kHz
 * (16 MHz / 8 / (1999+1)). PWM_CS is the TCCR1B CS12:CS10 prescaler field. */
#ifndef PWM_TOP
#define PWM_TOP 1999u
#endif
#ifndef PWM_CS
#define PWM_CS ((uint8_t)(1u << CS11)) /* /8 prescaler */
#endif

static uint16_t pwmDutyPermille;

void Pwm_Init(void)
{
    DDRB  |= (uint8_t)(1u << PB1);              /* OC1A output          */
    PORTB &= (uint8_t)~(1u << PB1);
    /* Boot at true 0%: OC1A disconnected (see Pwm_SetDutyCycle). */
    TCCR1A = (uint8_t)(1u << WGM11);
    TCCR1B = (uint8_t)((1u << WGM13) | (1u << WGM12) | (PWM_CS));
    ICR1   = PWM_TOP;                           /* mode 14: ICR1 = TOP  */
    OCR1A  = 0u;
    pwmDutyPermille = 0u;
}

void Pwm_SetDutyCycle(uint16_t permille)
{
    uint16_t ocr;

    if (permille > 1000u)
    {
        permille = 1000u;
    }
    pwmDutyPermille = permille;

    if (permille == 0u)
    {
        /* ATmega328P fast-PWM quirk: OCR1A = BOTTOM still emits a narrow spike
         * every TOP+1 cycles, so "duty 0" would never be fully off. Disconnect
         * OC1A from the waveform generator and drive the pin low - a true 0%. */
        TCCR1A &= (uint8_t)~(1u << COM1A1);
        PORTB  &= (uint8_t)~(1u << PB1);
        return;
    }

    TCCR1A |= (uint8_t)(1u << COM1A1); /* (re)connect OC1A */
    ocr = (uint16_t)(((uint32_t)permille * PWM_TOP) / 1000u);

    /* 16-bit timer registers share the TEMP byte; keep the two-byte write
     * indivisible against any future ISR that might also do 16-bit accesses. */
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        OCR1A = ocr;
    }
}

uint16_t Pwm_GetDutyCycle(void)
{
    return pwmDutyPermille;
}
