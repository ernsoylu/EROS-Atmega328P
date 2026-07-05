/**
 * @file    spi.c
 * @brief   SPI master driver implementation (see spi.h).
 */

#include <avr/io.h>

#include "spi.h"

void SPI_Init(uint8_t mode, uint8_t clock)
{
    /* SS must be a (high) output BEFORE enabling master mode: a low
     * input on SS clears MSTR in hardware (mode-fault protection). */
    PORTB |= (uint8_t)(1u << PB2);
    DDRB  |= (uint8_t)((1u << PB2) | (1u << PB3) | (1u << PB5));
    DDRB  &= (uint8_t)~(1u << PB4); /* MISO input */

    SPCR = (uint8_t)((1u << SPE) | (1u << MSTR) |
                     ((mode & 0x03u) << CPHA) | (clock & 0x03u));
    SPSR = (uint8_t)(((clock & 0x04u) != 0u) ? (1u << SPI2X) : 0u);
}

uint8_t SPI_Transfer(uint8_t byte)
{
    SPDR = byte;
    while ((SPSR & (uint8_t)(1u << SPIF)) == 0u)
    {
        /* 8 bit times, hardware-bounded (1..64 us) */
    }
    return SPDR;
}

void SPI_TransferBuf(uint8_t *buf, uint8_t len)
{
    uint8_t i;

    for (i = 0u; i < len; i++)
    {
        buf[i] = SPI_Transfer(buf[i]);
    }
}
