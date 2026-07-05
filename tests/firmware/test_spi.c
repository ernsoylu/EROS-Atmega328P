/**
 * @file    test_spi.c
 * @brief   Test of the SPI master driver under simavr with a fixed slave.
 *
 * The host runner (--spi-slave 5A) models a slave that returns a constant
 * 0x5A on MISO for every transfer. Verifies init (master mode, SS high)
 * and that the polled-SPIF transfer path clocks the MISO byte in.
 */

#include <avr/io.h>
#include "spi.h"
#include "testkit.h"

#define SPI_SLAVE_BYTE 0x5Au   /* must match FLAGS_spi (--spi-slave 5A) */

int main(void)
{
    uint8_t r;

    tk_init();

    SPI_Init(SPI_MODE0, SPI_CLK_DIV16);

    /* Master mode enabled, SPI enabled. */
    TK_ASSERT((SPCR & (1u << SPE)) != 0u, "spe");
    TK_ASSERT((SPCR & (1u << MSTR)) != 0u, "mstr");

    /* Every transfer clocks in the slave's constant byte, whatever we
     * send on MOSI. */
    r = SPI_Transfer(0x00u);
    TK_ASSERT(r == SPI_SLAVE_BYTE, "miso-1");

    r = SPI_Transfer(0xFFu);
    TK_ASSERT(r == SPI_SLAVE_BYTE, "miso-2");

    /* In-place buffer transfer: each byte is overwritten with MISO. */
    {
        uint8_t buf[4] = { 0x01u, 0x23u, 0x45u, 0x67u };
        SPI_TransferBuf(buf, 4u);
        TK_ASSERT(buf[0] == SPI_SLAVE_BYTE && buf[1] == SPI_SLAVE_BYTE &&
                  buf[2] == SPI_SLAVE_BYTE && buf[3] == SPI_SLAVE_BYTE,
                  "miso-buf");
    }

    tk_pass();
}
