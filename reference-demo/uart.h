/**
 * @file    uart.h
 * @brief   Interrupt-driven, non-blocking UART driver.
 *
 * A naive POLLED driver (busy-waiting on UDRE0 for every byte) blocks
 * the CPU for ~1 ms per character at 9600 baud. Inside an RTOS that
 * would stall every other task, so this driver is fully
 * interrupt-driven and non-blocking:
 *
 *   TX: UART_PutChar()/UART_Print*() enqueue into a ring buffer; the
 *       USART_UDRE ISR drains it in the background. If the ring is full
 *       the byte is DROPPED (and counted) - a task never busy-waits.
 *   RX: the USART_RX ISR captures bytes into a second ring; tasks poll
 *       with UART_GetChar().
 *
 * ISR category (OSEK): both UART ISRs are Category 1 - they touch only
 * the rings and the UART hardware and MUST NOT call any OS service.
 * (Contrast with the kernel's Timer2 tick ISR, which is Category 2.)
 *
 * Concurrency: classic single-producer/single-consumer rings with
 * one-byte head/tail indices. On AVR an 8-bit load/store is atomic, and
 * each side owns exactly one index, so no further locking is needed;
 * the UDRIE0 read-modify-write is the one shared spot and is guarded.
 *
 * String literals: use the _P variants with PSTR() so constant text
 * stays in Flash (zero RAM cost).
 */

#ifndef UART_H
#define UART_H

#include <stdint.h>
#include <avr/pgmspace.h>

/** Initialise 9600 8N1, RX interrupt enabled, TX ring idle.
 *  Call with interrupts disabled (e.g. from StartupHook()). */
void UART_Init(void);

/** Enqueue one byte for background transmission.
 *  @return 1 = queued, 0 = ring full (byte dropped and counted). */
uint8_t UART_PutChar(char c);

/** Enqueue a RAM string. */
void UART_Print(const char *s);

/** Enqueue a PROGMEM string (use with PSTR("...")). */
void UART_Print_P(PGM_P s);

/** Enqueue an unsigned 16-bit value in decimal. */
void UART_PrintU16(uint16_t value);

/** Enqueue an 8-bit value as two hex digits. */
void UART_PrintHex8(uint8_t value);

/** Fetch one received byte if available.
 *  @return 1 = *c valid, 0 = RX ring empty. */
uint8_t UART_GetChar(char *c);

/** Number of TX bytes dropped because the ring was full (diagnostic). */
uint8_t UART_TxDropped(void);

#endif /* UART_H */
