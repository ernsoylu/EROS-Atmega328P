/**
 * @file    test_i2c.c
 * @brief   Test of the TWI (I2C) master driver under simavr.
 *
 * With no slave on the simulated bus, every transaction must fail
 * cleanly via the driver's bounded-timeout / NACK paths - it must never
 * return I2C_OK and must never hang (that is the whole point of the
 * WCET-capped spins). This verifies TWI init plus the error path without
 * needing a host-side virtual slave.
 */

#include <avr/io.h>
#include "i2c.h"
#include "testkit.h"

int main(void)
{
    uint8_t r;

    tk_init();

    I2C_Init();

    /* TWI must actually be enabled after init. */
    TK_ASSERT((TWCR & (1u << TWEN)) != 0u, "twen");

    /* Probe of an absent address: START may succeed, addressing must not
     * be ACKed -> not I2C_OK, and the call must return (no hang). */
    r = I2C_Probe(0x50u);
    TK_ASSERT(r != I2C_OK, "probe-nack");

    /* A register write to the same absent slave must also fail cleanly. */
    {
        const uint8_t data[2] = { 0x11u, 0x22u };
        r = I2C_WriteRegs(0x50u, 0x00u, data, 2u);
        TK_ASSERT(r != I2C_OK, "write-nack");
    }

    /* And a read. */
    {
        uint8_t rx[2];
        r = I2C_ReadRegs(0x50u, 0x00u, rx, 2u);
        TK_ASSERT(r != I2C_OK, "read-nack");
    }

    tk_pass();
}
