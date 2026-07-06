/**
 * @file    adc.h
 * @brief   ADC driver - blocking single conversions, AVcc reference.
 *
 * Pins: A0..A5 = PC0..PC5 (dual-purpose), A6/A7 = ADC6/ADC7 (Nano:
 * analog-only, no digital function). Internal channels: 1.1 V bandgap
 * and the on-die temperature sensor.
 *
 * Timing/WCET: ADC clock = F_CPU/128 = 125 kHz; one conversion is
 * 13 ADC cycles = ~104 us, first conversion after enable/reference
 * change 25 cycles = ~200 us. The busy-wait is hardware-bounded, so a
 * blocking read is fine inside a task (budget ~0.2 ms per sample).
 * No ISR is used (OSEK: no category concerns).
 *
 * Ownership: this driver assumes it is the only ADC user. It restores
 * ADMUX to AVcc/channel-0 after internal-channel reads.
 */

#ifndef ADC_H
#define ADC_H

#include <stdint.h>

/** Enable the ADC: AVcc reference, /128 prescaler (125 kHz ADC clock).
 *  Call with interrupts disabled (e.g. from StartupHook()).
 *  Tip: for lower noise/power set DIDR0 bits for pins used purely as
 *  analog inputs (kills their digital input buffer) - app decision. */
void Adc_Init(void);

/** Blocking single conversion on channel 0..7 (A0..A7), ~104 us.
 *  @return raw 10-bit result 0..1023 (channel is masked to 0..7). */
uint16_t Adc_ReadChannel(uint8_t channel);

/** Measure the supply voltage by converting the internal 1.1 V bandgap
 *  against AVcc (no external parts): Vcc[mV] = 1100 * 1024 / raw.
 *  ~350 us (settle + discarded conversion + real conversion).
 *  @return Vcc in millivolts, or 0 if the reading was invalid. */
uint16_t Adc_ReadVccMillivolts(void);

/** Raw reading of the on-die temperature sensor (channel 8) against
 *  the internal 1.1 V reference. UNCALIBRATED: ~314 mV at +25 C,
 *  ~1.22 mV/K slope, device-to-device offset up to +/-10 C - calibrate
 *  per board and store the offset in EEPROM. ~350 us. */
uint16_t Adc_ReadTempRaw(void);

#endif /* ADC_H */
