/**
 * @file    uart_print.c
 * @brief   Console formatting helpers shared by every console backend.
 *
 * The hardware-independent `Uart_Print*` formatters are pure wrappers over
 * `Uart_PutChar()`, so they are identical whether the byte sink is a USART
 * (`uart.c`) or native USB CDC (`drivers/mcal/usb_cdc.c`). They live here once,
 * and each transport backend links this file alongside its own
 * Init/PutChar/GetChar. Depends only on `uart.h` (the `Uart_PutChar` contract).
 */
#include <avr/pgmspace.h>

#include "uart.h"

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
