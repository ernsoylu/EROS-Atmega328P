/**
 * @file    test_uart.c
 * @brief   Test of the interrupt-driven UART driver under simavr.
 *
 * This is the one test that reports THROUGH the driver under test rather
 * than the polled testkit channel: the driver enqueues the verdict into
 * its TX ring and the USART_UDRE ISR drains it byte-by-byte. If the ring
 * indexing or the ISR were broken, the host would never see a well-formed
 * "EROS-TEST: PASS" line, so the transmit path is exercised end-to-end.
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/delay.h>
#include "uart.h"

/* Spin with interrupts enabled long enough for the UDRE ISR to fully
 * drain the TX ring onto the (simulated) wire. */
static void drain(void)
{
    _delay_ms(50);
}

int main(void)
{
    Uart_Init();
    sei();

    /* Exercise the formatting helpers, then the verdict line. Every byte
     * travels ring -> UDRE ISR -> UDR0, which is what we are testing. */
    Uart_Print("uart u16=");
    Uart_PrintU16(12345u);
    Uart_Print(" hex=");
    Uart_PrintHex8(0xB7u);
    Uart_PutChar('\n');
    drain();

    /* A bounded print must not have dropped anything (ring >= message). */
    if (Uart_TxDropped() != 0u)
    {
        Uart_Print("EROS-TEST: FAIL tx-dropped\n");
        drain();
        for (;;) { /* halt */ }
    }

    Uart_Print("EROS-TEST: PASS\n");
    drain();

    for (;;) { /* halt */ }
}
