/**
 * @file    testkit.h
 * @brief   On-chip unit-test harness for EROS firmware under simavr.
 *
 * A test is an AVR firmware image that runs its own assertions on real
 * hardware registers inside the simulator and reports the verdict over
 * USART0 as a single sentinel line the host runner greps for:
 *
 *     EROS-TEST: PASS
 *     EROS-TEST: FAIL <tag>
 *
 * The report channel is a tiny POLLED UART TX (busy-wait on UDRE0) that
 * is deliberately independent of the interrupt-driven production driver
 * (comprehensive-demo/uart.c) so a test can exercise that driver without
 * fighting the harness for UDR0. The only exception is test_uart.c,
 * which reports *through* the driver on purpose.
 *
 * Usage:
 *     #include "testkit.h"
 *     int main(void) {
 *         tk_init();
 *         TK_ASSERT(EE_ReadByte(0) == 0xFF, "ee-erased");
 *         ... more asserts ...
 *         tk_pass();          // never returns
 *     }
 *
 * The first failing TK_ASSERT prints FAIL and halts; reaching tk_pass()
 * means every assertion held.
 */

#ifndef TESTKIT_H
#define TESTKIT_H

#include <stdint.h>

/** Init polled USART0 TX (9600 8N1, TX only) for the report channel. */
void tk_init(void);

/** Emit one byte (busy-waits on UDRE0). */
void tk_putc(char c);

/** Emit a RAM string. */
void tk_print(const char *s);

/** Emit an unsigned decimal (diagnostic breadcrumb before a verdict). */
void tk_print_u16(uint16_t v);

/** Print "EROS-TEST: PASS" and halt. Never returns. */
void tk_pass(void) __attribute__((noreturn));

/** Print "EROS-TEST: FAIL <tag>" and halt. Never returns. */
void tk_fail(const char *tag) __attribute__((noreturn));

/**
 * Assertion: on failure, report `tag` and halt the image. `tag` should
 * be a short kebab identifier unique within the test so a CI failure
 * names the exact check that broke.
 */
#define TK_ASSERT(cond, tag)          \
    do {                              \
        if (!(cond)) tk_fail(tag);    \
    } while (0)

#endif /* TESTKIT_H */
