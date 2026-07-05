/**
 * @file    i2c.h
 * @brief   TWI (I2C) master driver - blocking with bounded timeouts.
 *
 * Pins: SDA = PC4/A4, SCL = PC5/A5 (external pull-ups required, 4.7k
 * typical; A4/A5 are then lost as ADC channels). Bus clock 100 kHz.
 *
 * Timing/WCET: at 100 kHz one byte on the wire takes ~90 us. Every
 * wait-for-TWINT spin is capped, so a wedged bus cannot hang a task:
 * worst case per transaction ~ (len + 2) * ~1 ms timeout budget, normal
 * case (len + 2) * ~0.1 ms. A register write/read of a few bytes fits
 * comfortably in a 1-2 tick WCET budget.
 *
 * No ISR is used (polled TWINT - OSEK: no category concerns). Clock
 * stretching by slaves is supported (that is what the timeout bounds).
 * Multi-master arbitration is NOT supported: single-master bus only.
 */

#ifndef I2C_H
#define I2C_H

#include <stdint.h>

/* Status codes (uint8_t): */
#define I2C_OK            0u
#define I2C_ERR_START     1u /* bus error / arbitration on START       */
#define I2C_ERR_ADDR_NACK 2u /* no slave answered the address          */
#define I2C_ERR_DATA_NACK 3u /* slave rejected a data byte             */
#define I2C_ERR_TIMEOUT   4u /* SCL held / bus wedged (spin cap hit)   */

/** Configure TWI for 100 kHz. Call with interrupts disabled
 *  (e.g. from StartupHook()). Pull-ups are external. */
void I2C_Init(void);

/** Address-only probe (START + SLA+W + STOP).
 *  @return I2C_OK if a slave ACKed addr7 (7-bit address, unshifted). */
uint8_t I2C_Probe(uint8_t addr7);

/** Write len bytes to a register: START, SLA+W, reg, data..., STOP. */
uint8_t I2C_WriteRegs(uint8_t addr7, uint8_t reg,
                      const uint8_t *data, uint8_t len);

/** Read len bytes from a register: START, SLA+W, reg, REPEATED START,
 *  SLA+R, data..., STOP. */
uint8_t I2C_ReadRegs(uint8_t addr7, uint8_t reg,
                     uint8_t *data, uint8_t len);

#endif /* I2C_H */
