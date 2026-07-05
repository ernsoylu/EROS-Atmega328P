/**
 * @file    testkit.c
 * @brief   Implementation of the on-chip test harness (see testkit.h).
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <avr/sleep.h>

#include "testkit.h"

#ifndef TK_BAUD
#define TK_BAUD 9600UL
#endif
#define TK_UBRR ((F_CPU / (16UL * TK_BAUD)) - 1UL)

void tk_init(void)
{
    UBRR0H = (uint8_t)(TK_UBRR >> 8);
    UBRR0L = (uint8_t)TK_UBRR;
    UCSR0A = 0u;                                            /* U2X off */
    UCSR0C = (uint8_t)((1u << UCSZ01) | (1u << UCSZ00));    /* 8N1     */
    UCSR0B = (uint8_t)(1u << TXEN0);                        /* TX only */
}

void tk_putc(char c)
{
    while ((UCSR0A & (uint8_t)(1u << UDRE0)) == 0u) { /* wait for TX ready */ }
    UDR0 = (uint8_t)c;
}

void tk_print(const char *s)
{
    while (*s != '\0')
    {
        tk_putc(*s);
        s++;
    }
}

void tk_print_u16(uint16_t v)
{
    char    d[5];
    uint8_t n = 0u;

    do {
        d[n++] = (char)('0' + (uint8_t)(v % 10u));
        v /= 10u;
    } while (v != 0u);

    while (n != 0u)
    {
        n--;
        tk_putc(d[n]);
    }
}

/* Drain the last byte, then park the CPU. simavr reports a sleeping CPU
 * so the host runner can stop even if it missed the sentinel newline. */
static void tk_halt(void) __attribute__((noreturn));
static void tk_halt(void)
{
    while ((UCSR0A & (uint8_t)(1u << TXC0)) == 0u &&
           (UCSR0A & (uint8_t)(1u << UDRE0)) == 0u) { /* let last byte drain */ }
    cli();
    set_sleep_mode(SLEEP_MODE_PWR_DOWN);
    sleep_enable();
    for (;;)
    {
        sleep_cpu();
    }
}

void tk_pass(void)
{
    tk_print("EROS-TEST: PASS\n");
    tk_halt();
}

void tk_fail(const char *tag)
{
    tk_print("EROS-TEST: FAIL ");
    tk_print(tag);
    tk_putc('\n');
    tk_halt();
}
