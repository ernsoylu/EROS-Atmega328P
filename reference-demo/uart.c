/**
 * @file    uart.c
 * @brief   Interrupt-driven UART driver implementation (see uart.h).
 *
 * Both ISRs here are OSEK Category 1: no OS service calls, minimal
 * length, hardware + ring buffer access only.
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/atomic.h>

#include "uart.h"
#include "uart_regs.h"   /* UART_* aliases: USART0 (328P/2560) or USART1 (32U4) */

/* Baud and ring sizes are overridable from the build system (the
 * erosgen configurator emits -D flags from app.yaml); the defaults
 * below preserve the original driver behaviour. */
#ifndef UART_BAUD
#define UART_BAUD      9600UL
#endif
#define UART_UBRR      ((F_CPU / (16UL * UART_BAUD)) - 1UL)

/* Ring sizes must be powers of two (index arithmetic uses masks) and
 * fit the 8-bit indices (2..256). RX is sized so that pasted input
 * survives: at 9600 baud a full 50 ms command-task period delivers at
 * most 48 bytes, and 48 < 63 usable slots, so no byte is lost even
 * during a continuous paste as long as the consumer drains the ring
 * every period. Rings are the dominant application RAM cost - size
 * them to the wire math, not generously. */
#ifndef UART_TX_SIZE
#define UART_TX_SIZE   128u
#endif
#ifndef UART_RX_SIZE
#define UART_RX_SIZE   64u
#endif

#if (UART_TX_SIZE & (UART_TX_SIZE - 1u)) != 0u || (UART_TX_SIZE > 256u)
#error "UART_TX_SIZE must be a power of two, 2..256"
#endif
#if (UART_RX_SIZE & (UART_RX_SIZE - 1u)) != 0u || (UART_RX_SIZE > 256u)
#error "UART_RX_SIZE must be a power of two, 2..256"
#endif

#define TX_SIZE        UART_TX_SIZE
#define TX_MASK        (TX_SIZE - 1u)
#define RX_SIZE        UART_RX_SIZE
#define RX_MASK        (RX_SIZE - 1u)

/* TX: tasks produce (head), UDRE ISR consumes (tail). */
static volatile uint8_t txBuf[TX_SIZE];
static volatile uint8_t txHead;
static volatile uint8_t txTail;
static volatile uint8_t txDropped;

/* RX: RX ISR produces (head), tasks consume (tail). */
static volatile uint8_t rxBuf[RX_SIZE];
static volatile uint8_t rxHead;
static volatile uint8_t rxTail;

void Uart_Init(void)
{
    UART_UBRRH = (uint8_t)(UART_UBRR >> 8);
    UART_UBRRL = (uint8_t)UART_UBRR;
    UART_UCSRA = 0u;                                  /* U2X off        */
    UART_UCSRC = (uint8_t)((1u << UART_UCSZ1) | (1u << UART_UCSZ0)); /* 8N1 */
    UART_UCSRB = (uint8_t)((1u << UART_RXEN) | (1u << UART_TXEN)
                           | (1u << UART_RXCIE));
    /* UDRIE is enabled on demand by Uart_PutChar(). */
}

/** Category 1 ISR: transmit ring drain. */
ISR(UART_UDRE_VECT)
{
    if (txTail == txHead)
    {
        UART_UCSRB &= (uint8_t)~(1u << UART_UDRIE); /* ring empty: TX IRQ off */
    }
    else
    {
        UART_UDR = txBuf[txTail];
        txTail   = (uint8_t)((txTail + 1u) & TX_MASK);
    }
}

/** Category 1 ISR: receive capture. Overrun policy: drop newest byte. */
ISR(UART_RX_VECT)
{
    const uint8_t data = UART_UDR; /* always read: clears RXC */
    const uint8_t next = (uint8_t)((rxHead + 1u) & RX_MASK);

    if (next != rxTail)
    {
        rxBuf[rxHead] = data;
        rxHead        = next;
    }
}

uint8_t Uart_PutChar(char c)
{
    uint8_t ok = 1u;
    const uint8_t next = (uint8_t)((txHead + 1u) & TX_MASK);

    if (next == txTail)
    {
        txDropped++; /* never block a task on the wire */
        ok = 0u;
    }
    else
    {
        txBuf[txHead] = (uint8_t)c;
        txHead        = next;

        /* UART_UCSRB is also written by the UDRE ISR (it clears UDRIE on
         * ring-empty); guard the read-modify-write. */
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            UART_UCSRB |= (uint8_t)(1u << UART_UDRIE);
        }
    }
    return ok;
}

void Uart_Print(const char *s)
{
    while (*s != '\0')
    {
        (void)Uart_PutChar(*s);
        s++;
    }
}

void Uart_Print_P(PGM_P s)
{
    char c = (char)pgm_read_byte(s);

    while (c != '\0')
    {
        (void)Uart_PutChar(c);
        s++;
        c = (char)pgm_read_byte(s);
    }
}

void Uart_PrintU16(uint16_t value)
{
    char    digits[5]; /* 65535 -> max 5 digits */
    uint8_t n = 0u;

    do
    {
        digits[n] = (char)('0' + (uint8_t)(value % 10u));
        value /= 10u;
        n++;
    } while (value != 0u);

    while (n != 0u)
    {
        n--;
        (void)Uart_PutChar(digits[n]);
    }
}

void Uart_PrintHex8(uint8_t value)
{
    static const char hex[16] PROGMEM = "0123456789ABCDEF";

    (void)Uart_PutChar((char)pgm_read_byte(&hex[(value >> 4) & 0x0Fu]));
    (void)Uart_PutChar((char)pgm_read_byte(&hex[value & 0x0Fu]));
}

uint8_t Uart_GetChar(char *c)
{
    uint8_t ok = 0u;

    if (rxTail != rxHead)
    {
        *c     = (char)rxBuf[rxTail];
        rxTail = (uint8_t)((rxTail + 1u) & RX_MASK);
        ok     = 1u;
    }
    return ok;
}

uint8_t Uart_TxDropped(void)
{
    return txDropped;
}
