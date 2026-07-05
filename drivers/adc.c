/**
 * @file    adc.c
 * @brief   ADC driver implementation (see adc.h).
 */

#include <avr/io.h>

#include "adc.h"

#define ADC_MUX_BANDGAP 0x0Eu /* internal 1.1 V reference as input     */
#define ADC_MUX_TEMP    0x08u /* on-die temperature sensor             */

/* AVcc reference (REFS0), internal 1.1 V reference (REFS1|REFS0). */
#define ADC_REF_AVCC    (uint8_t)(1u << REFS0)
#define ADC_REF_1V1     (uint8_t)((1u << REFS1) | (1u << REFS0))

void ADC_Init(void)
{
    ADMUX  = ADC_REF_AVCC; /* channel 0 */
    ADCSRA = (uint8_t)((1u << ADEN) | (1u << ADPS2) |
                       (1u << ADPS1) | (1u << ADPS0)); /* /128 */
}

/** Start one conversion with the current ADMUX and wait for it
 *  (hardware-bounded: 13..25 ADC cycles). */
static uint16_t ADC_Convert(void)
{
    ADCSRA |= (uint8_t)(1u << ADSC);
    while ((ADCSRA & (uint8_t)(1u << ADSC)) != 0u)
    {
        /* 13 ADC cycles = ~104 us at 125 kHz */
    }
    return ADC;
}

uint16_t ADC_Read(uint8_t channel)
{
    ADMUX = (uint8_t)(ADC_REF_AVCC | (channel & 0x07u));
    return ADC_Convert();
}

/** Internal-channel read: switch MUX/reference, let the reference
 *  settle, discard the first (inaccurate) conversion, convert, then
 *  restore AVcc/channel 0. */
static uint16_t ADC_ReadInternal(uint8_t admux)
{
    uint16_t raw;

    ADMUX = admux;
    (void)ADC_Convert(); /* settle + discard (datasheet 24.5.2) */
    raw = ADC_Convert();

    ADMUX = ADC_REF_AVCC;
    return raw;
}

uint16_t ADC_ReadVccMillivolts(void)
{
    const uint16_t raw =
        ADC_ReadInternal((uint8_t)(ADC_REF_AVCC | ADC_MUX_BANDGAP));

    /* Vcc = Vbg * 1024 / raw; raw == 0 would mean an open mux. */
    return (raw != 0u) ? (uint16_t)(1126400uL / raw) : 0u;
}

uint16_t ADC_ReadTempRaw(void)
{
    return ADC_ReadInternal((uint8_t)(ADC_REF_1V1 | ADC_MUX_TEMP));
}
