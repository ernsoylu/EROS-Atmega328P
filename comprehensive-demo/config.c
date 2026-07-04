/**
 * @file    config.c
 * @brief   TinyOS static configuration tables for the comprehensive demo.
 *
 * Same layout rules as the root demo: const config in PROGMEM, dynamic
 * state lives in the kernel, pool arena is the only RAM contributed here.
 */

#include <avr/pgmspace.h>

#include "tiny_os.h"

const OsTaskConfigType OS_taskConfig[OS_NUM_TASKS] PROGMEM =
{
    [TASK_STARTUP] = { Task_Startup, 1u /* autostart */, 2u /* WCET */ },
    [TASK_STATUS]  = { Task_Status,  0u,                 2u },
    [TASK_RAMP]    = { Task_Ramp,    0u,                 1u },
    [TASK_CMD]     = { Task_Cmd,     0u,                 2u },
    [TASK_BUTTON]  = { Task_Button,  0u,                 1u },
};

const OsAlarmConfigType OS_alarmConfig[OS_NUM_ALARMS] PROGMEM =
{
    [ALARM_BUTTON] = { TASK_BUTTON },
    [ALARM_CMD]    = { TASK_CMD    },
    [ALARM_RAMP]   = { TASK_RAMP   },
    [ALARM_STATUS] = { TASK_STATUS },
};

const OsResourceConfigType OS_resourceConfig[OS_NUM_RESOURCES] PROGMEM =
{
    /* Ceiling = highest-priority user: PrintStatus() runs from both
     * Task_Status (prio 1) and Task_Cmd (prio 3, STAT command). */
    [RES_UART] = { TASK_CMD /* ceiling */, 0u /* no ISR mask */ },
};

uint8_t OS_poolArena[(uint16_t)OS_POOL_NUM_BLOCKS * OS_POOL_BLOCK_SIZE];
