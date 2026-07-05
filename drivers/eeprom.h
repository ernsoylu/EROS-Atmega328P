/**
 * @file    eeprom.h
 * @brief   EEPROM driver - wear-aware parameter storage (1 KiB).
 *
 * Update semantics: every write goes through read-compare-skip, so
 * unchanged bytes cost ~4 us instead of an erase+write cycle and do not
 * consume endurance (100k erase/write cycles per byte).
 *
 * Timing/WCET: an actual byte erase+write takes ~3.4 ms, during which
 * EE_Update busy-waits BEFORE the next byte (interrupts stay enabled -
 * the OS tick keeps running; only the 2-instruction EEMPE/EEPE arm
 * sequence runs with interrupts masked, as the datasheet requires).
 * Worst-case WCET of EE_Update(len) = len * 3.4 ms + 3.4 ms: budget it,
 * or write few bytes per task activation (e.g. one 8-byte parameter
 * block = ~31 ms -> belongs in a slow task or spread over activations).
 * Reads are ~4 us/byte, no wait if no write is in flight.
 *
 * No ISR is used. Ownership: sole EEPROM user; do not mix with
 * avr-libc <avr/eeprom.h> calls from elsewhere.
 */

#ifndef EEPROM_H
#define EEPROM_H

#include <stdint.h>

#define EE_SIZE 1024u /* ATmega328P: addresses 0..1023 */

/** Read one byte. Address out of range returns 0xFF (erased value). */
uint8_t EE_ReadByte(uint16_t addr);

/** Read len bytes into dst (clipped at the EEPROM end). */
void EE_Read(uint16_t addr, uint8_t *dst, uint16_t len);

/** Write one byte if it differs from the stored value (wear-aware).
 *  Busy-waits for a previous write first - see WCET note above. */
void EE_UpdateByte(uint16_t addr, uint8_t value);

/** Update len bytes from src (clipped at the EEPROM end). */
void EE_Update(uint16_t addr, const uint8_t *src, uint16_t len);

/** 1 = no write in flight (EE_ReadByte/EE_UpdateByte return at their
 *  minimum WCET), 0 = programming in progress (~3.4 ms remaining). */
uint8_t EE_IsReady(void);

#endif /* EEPROM_H */
