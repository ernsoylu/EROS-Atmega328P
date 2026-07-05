/**
 * @file    eeprom.c
 * @brief   EEPROM driver implementation (see eeprom.h).
 *
 * Implemented on the raw registers instead of <avr/eeprom.h> so the
 * EEMPE -> EEPE arm sequence (must complete within 4 CPU cycles,
 * datasheet 8.6.3) is explicitly interrupt-protected: a tick ISR firing
 * between the two stores would silently lose the write.
 */

#include <avr/io.h>
#include <util/atomic.h>

#include "eeprom.h"

/** Busy-wait until a previous programming cycle (if any) finished.
 *  Interrupts stay enabled - worst case ~3.4 ms of task time. */
static void EE_WaitReady(void)
{
    while ((EECR & (uint8_t)(1u << EEPE)) != 0u)
    {
        /* erase+write in progress */
    }
}

uint8_t EE_IsReady(void)
{
    return ((EECR & (uint8_t)(1u << EEPE)) == 0u) ? 1u : 0u;
}

uint8_t EE_ReadByte(uint16_t addr)
{
    uint8_t value = 0xFFu;

    if (addr < EE_SIZE)
    {
        EE_WaitReady();
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            EEAR  = addr;
            EECR |= (uint8_t)(1u << EERE); /* CPU halts 4 cycles */
            value = EEDR;
        }
    }
    return value;
}

void EE_Read(uint16_t addr, uint8_t *dst, uint16_t len)
{
    uint16_t i;

    for (i = 0u; i < len; i++)
    {
        dst[i] = EE_ReadByte(addr + i);
    }
}

void EE_UpdateByte(uint16_t addr, uint8_t value)
{
    if (addr < EE_SIZE)
    {
        if (EE_ReadByte(addr) != value) /* wear-aware: skip identical */
        {
            EE_WaitReady(); /* redundant after read, kept for clarity */
            ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
            {
                EEAR  = addr;
                EEDR  = value;
                /* EEPM1:0 = 00 (atomic erase+write, ~3.4 ms). The two
                 * stores below are the 4-cycle-critical arm sequence. */
                EECR  = (uint8_t)(1u << EEMPE);
                EECR |= (uint8_t)(1u << EEPE);
            }
            /* Programming continues in hardware; the next EEPROM call
             * waits for it. The task may terminate meanwhile. */
        }
    }
}

void EE_Update(uint16_t addr, const uint8_t *src, uint16_t len)
{
    uint16_t i;

    for (i = 0u; i < len; i++)
    {
        EE_UpdateByte(addr + i, src[i]);
    }
}
