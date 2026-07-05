/**
 * @file    asw_500ms.h
 * @brief   500 ms rate - scope channel + status line (TASK_STATUS), plus
 *          the chained 2 s heartbeat on the on-board LED (TASK_REPORT).
 */

#ifndef ASW_500MS_H
#define ASW_500MS_H

/** 500 ms task entry (released by ALARM_STATUS). */
extern void Task_Status(void);

/** Chained by every 4th TASK_STATUS run (effective period 2 s); also
 *  activated once at startup by the deliberate double-activation demo. */
extern void Task_Report(void);

#endif /* ASW_500MS_H */
