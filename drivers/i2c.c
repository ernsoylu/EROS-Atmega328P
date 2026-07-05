/**
 * @file    i2c.c
 * @brief   TWI (I2C) master driver implementation (see i2c.h).
 */

#include <avr/io.h>
#include <util/twi.h>

#include "i2c.h"

/* TWBR for 100 kHz: SCL = F_CPU / (16 + 2*TWBR*presc), presc = 1
 * -> TWBR = ((16 MHz / 100 kHz) - 16) / 2 = 72. */
#define I2C_TWBR_100KHZ 72u

/* Spin cap per TWINT wait. One loop iteration is a handful of cycles;
 * 8000 iterations = ~1.5 ms at 16 MHz - generous against slave clock
 * stretching, still a hard WCET bound. */
#define I2C_SPIN_MAX 8000u

/** Wait for TWINT with a bounded spin.
 *  @return TW_STATUS, or 0xFFu on timeout (not a valid TWI status). */
static uint8_t I2C_Wait(void)
{
    uint16_t spin;

    for (spin = I2C_SPIN_MAX; spin != 0u; spin--)
    {
        if ((TWCR & (uint8_t)(1u << TWINT)) != 0u)
        {
            return TW_STATUS;
        }
    }
    return 0xFFu;
}

static void I2C_Stop(void)
{
    TWCR = (uint8_t)((1u << TWINT) | (1u << TWSTO) | (1u << TWEN));
    /* TWSTO clears itself when the STOP has been sent; no TWINT is
     * raised for STOP, so there is nothing to wait on. */
}

/** START (or repeated START) + expected status check. */
static uint8_t I2C_Start(void)
{
    uint8_t st;

    TWCR = (uint8_t)((1u << TWINT) | (1u << TWSTA) | (1u << TWEN));
    st = I2C_Wait();
    if (st == 0xFFu)
    {
        return I2C_ERR_TIMEOUT;
    }
    return ((st == TW_START) || (st == TW_REP_START)) ? I2C_OK
                                                      : I2C_ERR_START;
}

/** Transmit one byte (SLA or data), return the resulting TW_STATUS or
 *  0xFF on timeout. */
static uint8_t I2C_Tx(uint8_t byte)
{
    TWDR = byte;
    TWCR = (uint8_t)((1u << TWINT) | (1u << TWEN));
    return I2C_Wait();
}

/** Address the slave; failure paths release the bus with STOP. */
static uint8_t I2C_Sla(uint8_t sla)
{
    const uint8_t st = I2C_Tx(sla);

    if (st == 0xFFu)
    {
        I2C_Stop();
        return I2C_ERR_TIMEOUT;
    }
    if ((st != TW_MT_SLA_ACK) && (st != TW_MR_SLA_ACK))
    {
        I2C_Stop();
        return I2C_ERR_ADDR_NACK;
    }
    return I2C_OK;
}

void I2C_Init(void)
{
    TWSR = 0u;               /* prescaler 1                             */
    TWBR = I2C_TWBR_100KHZ;
    TWCR = (uint8_t)(1u << TWEN);
}

uint8_t I2C_Probe(uint8_t addr7)
{
    uint8_t rc = I2C_Start();

    if (rc == I2C_OK)
    {
        rc = I2C_Sla((uint8_t)(addr7 << 1)); /* SLA+W */
        if (rc == I2C_OK)
        {
            I2C_Stop();
        }
    }
    else
    {
        I2C_Stop();
    }
    return rc;
}

uint8_t I2C_WriteRegs(uint8_t addr7, uint8_t reg,
                      const uint8_t *data, uint8_t len)
{
    uint8_t rc = I2C_Start();
    uint8_t i;
    uint8_t st;

    if (rc != I2C_OK)
    {
        I2C_Stop();
        return rc;
    }
    rc = I2C_Sla((uint8_t)(addr7 << 1)); /* SLA+W */
    if (rc != I2C_OK)
    {
        return rc; /* bus already released */
    }

    st = I2C_Tx(reg);
    for (i = 0u; (i < len) && (st == TW_MT_DATA_ACK); i++)
    {
        st = I2C_Tx(data[i]);
    }
    I2C_Stop();

    if (st == 0xFFu)
    {
        return I2C_ERR_TIMEOUT;
    }
    return (st == TW_MT_DATA_ACK) ? I2C_OK : I2C_ERR_DATA_NACK;
}

uint8_t I2C_ReadRegs(uint8_t addr7, uint8_t reg,
                     uint8_t *data, uint8_t len)
{
    uint8_t rc = I2C_Start();
    uint8_t i;
    uint8_t st;

    if (rc != I2C_OK)
    {
        I2C_Stop();
        return rc;
    }
    rc = I2C_Sla((uint8_t)(addr7 << 1)); /* SLA+W: set register pointer */
    if (rc != I2C_OK)
    {
        return rc;
    }
    st = I2C_Tx(reg);
    if (st != TW_MT_DATA_ACK)
    {
        I2C_Stop();
        return (st == 0xFFu) ? I2C_ERR_TIMEOUT : I2C_ERR_DATA_NACK;
    }

    rc = I2C_Start(); /* repeated START */
    if (rc != I2C_OK)
    {
        I2C_Stop();
        return rc;
    }
    rc = I2C_Sla((uint8_t)((addr7 << 1) | 1u)); /* SLA+R */
    if (rc != I2C_OK)
    {
        return rc;
    }

    for (i = 0u; i < len; i++)
    {
        /* ACK every byte except the last (NACK terminates the read). */
        if (i < (uint8_t)(len - 1u))
        {
            TWCR = (uint8_t)((1u << TWINT) | (1u << TWEN) | (1u << TWEA));
        }
        else
        {
            TWCR = (uint8_t)((1u << TWINT) | (1u << TWEN));
        }
        st = I2C_Wait();
        if ((st != TW_MR_DATA_ACK) && (st != TW_MR_DATA_NACK))
        {
            I2C_Stop();
            return (st == 0xFFu) ? I2C_ERR_TIMEOUT : I2C_ERR_DATA_NACK;
        }
        data[i] = TWDR;
    }
    I2C_Stop();
    return I2C_OK;
}
