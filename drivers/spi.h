/**
 * @file    spi.h
 * @brief   SPI master driver - blocking full-duplex transfers.
 *
 * Pins: SCK = PB5/D13 (the on-board LED! see below), MISO = PB4/D12,
 * MOSI = PB3/D11, SS = PB2/D10. SPI_Init() drives SS/PB2 as a high
 * output - REQUIRED in master mode (a low input on SS drops the
 * hardware back to slave, datasheet 19.3.2); it doubles as a default
 * chip select. Additional chip selects are plain GPIOs owned by the
 * application.
 *
 * Conflicts: PB5 is shared between SCK and the Nano's on-board LED -
 * with SPI active the LED flickers with traffic and is unusable as a
 * status LED (both demos use PB5 for hooks/heartbeat: move that to
 * another pin if you enable SPI).
 *
 * Timing/WCET: blocking, hardware-bounded - one byte takes 8 bit times
 * (1 us at F_CPU/2 ... 64 us at F_CPU/128). A 32-byte burst at /16 is
 * ~64 us: bound bursts per activation, not the driver.
 *
 * No ISR is used (polled SPIF - OSEK: no category concerns).
 */

#ifndef SPI_H
#define SPI_H

#include <stdint.h>

/* SPI mode (CPOL/CPHA): */
#define SPI_MODE0 0u /* idle low,  sample on leading edge  (most common) */
#define SPI_MODE1 1u /* idle low,  sample on trailing edge */
#define SPI_MODE2 2u /* idle high, sample on leading edge  */
#define SPI_MODE3 3u /* idle high, sample on trailing edge */

/* SCK rate (encoding: bit2 = SPI2X, bits1:0 = SPR1:0): */
#define SPI_CLK_DIV4   0u /* 4 MHz  */
#define SPI_CLK_DIV16  1u /* 1 MHz  */
#define SPI_CLK_DIV64  2u /* 250 kHz */
#define SPI_CLK_DIV128 3u /* 125 kHz */
#define SPI_CLK_DIV2   4u /* 8 MHz  */
#define SPI_CLK_DIV8   5u /* 2 MHz  */
#define SPI_CLK_DIV32  6u /* 500 kHz */

/** Configure SPI master: mode SPI_MODE0..3, clock SPI_CLK_DIV*,
 *  MSB first. Sets SCK/MOSI/SS as outputs (SS high), MISO as input.
 *  Call with interrupts disabled (e.g. from StartupHook()). */
void SPI_Init(uint8_t mode, uint8_t clock);

/** Transmit one byte, return the byte clocked in simultaneously. */
uint8_t SPI_Transfer(uint8_t byte);

/** Full-duplex in-place transfer: buf is sent and overwritten with the
 *  received bytes. Chip select is the caller's job. */
void SPI_TransferBuf(uint8_t *buf, uint8_t len);

#endif /* SPI_H */
