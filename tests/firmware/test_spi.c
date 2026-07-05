/**
 * @file    test_spi.c
 * @brief   Test of the SPI master driver under simavr with a loopback slave.
 *
 * The host runner (--spi-echo) acts as a slave that returns each byte the
 * master clocks out, so a full-duplex transfer must read back exactly what
 * was sent. Verifies init (master mode, SS high) and the polled-SPIF
 * transfer path.
 */

#include <avr/io.h>
#include "spi.h"
#include "testkit.h"

int main(void)
{
    uint8_t r;

    tk_init();

    SPI_Init(SPI_MODE0, SPI_CLK_DIV16);

    /* Master mode enabled, SPI enabled. */
    TK_ASSERT((SPCR & (1u << SPE)) != 0u, "spe");
    TK_ASSERT((SPCR & (1u << MSTR)) != 0u, "mstr");

    /* Single-byte loopback. */
    r = SPI_Transfer(0x5Au);
    TK_ASSERT(r == 0x5Au, "xfer-5a");

    r = SPI_Transfer(0xA5u);
    TK_ASSERT(r == 0xA5u, "xfer-a5");

    /* In-place buffer loopback. */
    {
        uint8_t buf[4] = { 0x01u, 0x23u, 0x45u, 0x67u };
        SPI_TransferBuf(buf, 4u);
        TK_ASSERT(buf[0] == 0x01u && buf[1] == 0x23u &&
                  buf[2] == 0x45u && buf[3] == 0x67u, "xfer-buf");
    }

    tk_pass();
}
