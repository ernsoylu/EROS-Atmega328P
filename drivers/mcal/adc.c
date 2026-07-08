/**
 * @file    adc.c
 * @brief   ADC driver implementation (see adc.h).
 */

#include <avr/io.h>

#include "adc.h"

#define ADC_MUX_BANDGAP 0x0Eu /* internal 1.1 V reference as input     */
#define ADC_MUX_TEMP    0x08u /* on-die temperature sensor             */

/* AVcc reference (REFS0), internal 1.1 V (REFS1|REFS0), external AREF (0). */
#define ADC_REF_AVCC    (uint8_t)(1u << REFS0)
#define ADC_REF_1V1     (uint8_t)((1u << REFS1) | (1u << REFS0))
#define ADC_REF_AREF    (uint8_t)0u

/* Configurable reference + prescaler: erosgen overrides these with
 * -DADC_REF=ADC_REF_* / -DADC_PRESCALER=<ADPS field> from peripherals.adc; the
 * defaults reproduce the historical AVcc / 125 kHz (F_CPU/128). */
#ifndef ADC_REF
#define ADC_REF ADC_REF_AVCC
#endif
#ifndef ADC_PRESCALER
#define ADC_PRESCALER ((1u << ADPS2) | (1u << ADPS1) | (1u << ADPS0)) /* /128 */
#endif

void Adc_Init(void)
{
    ADMUX  = ADC_REF; /* channel 0 */
    ADCSRA = (uint8_t)((1u << ADEN) | ADC_PRESCALER);
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

uint16_t Adc_ReadChannel(uint8_t channel)
{
    ADMUX = (uint8_t)(ADC_REF | (channel & 0x07u));
    return ADC_Convert();
}

/** Internal-channel read: switch MUX/reference, let the reference
 *  settle, discard the first (inaccurate) conversion, convert, then
 *  restore the configured reference / channel 0. */
static uint16_t Adc_ReadChannelInternal(uint8_t admux)
{
    uint16_t raw;

    ADMUX = admux;
    (void)ADC_Convert(); /* settle + discard (datasheet 24.5.2) */
    raw = ADC_Convert();

    ADMUX = ADC_REF;
    return raw;
}

uint16_t Adc_ReadVccMillivolts(void)
{
    const uint16_t raw =
        Adc_ReadChannelInternal((uint8_t)(ADC_REF_AVCC | ADC_MUX_BANDGAP));

    /* Vcc = Vbg * 1024 / raw; raw == 0 would mean an open mux. */
    return (raw != 0u) ? (uint16_t)(1126400uL / raw) : 0u;
}

uint16_t Adc_ReadTempRaw(void)
{
    return Adc_ReadChannelInternal((uint8_t)(ADC_REF_1V1 | ADC_MUX_TEMP));
}

/* --- Cyclic sampling: AUTOSAR-style MainFunction -------------------------- */
/* Non-blocking channel-0 sampler for periodic scheduling: latch the previous
 * conversion, then kick the next. Wire it with `peripherals.adc.main_function_ms`
 * in app.yaml (erosgen calls it from the matching-rate ASW task); poll the
 * freshest value with Adc_GetLastSample(). */
static volatile uint16_t adc_last_sample;

uint16_t Adc_GetLastSample(void)
{
    return adc_last_sample;
}

void Adc_MainFunction(void)
{
    if ((ADCSRA & (uint8_t)(1u << ADSC)) == 0u) /* previous conversion done */
    {
        adc_last_sample = ADC;                  /* latch (non-blocking) */
        ADMUX = ADC_REF;                        /* channel 0 */
        ADCSRA |= (uint8_t)(1u << ADSC);        /* kick the next */
    }
}
