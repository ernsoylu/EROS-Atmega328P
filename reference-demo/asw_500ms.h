/**
 * @file    asw_500ms.h
 * @brief   500 ms rate - scope channel + ChainTask demo (TASK_SLOW),
 *          plus the chained 2 s heartbeat (TASK_REPORT).
 */

#ifndef ASW_500MS_H
#define ASW_500MS_H

/** 500 ms task entry (released by ALARM_SLOW). */
extern void Task_Slow(void);

/** Chained by every 4th TASK_SLOW run (effective period 2 s); also
 *  activated once at startup by the double-activation demo. */
extern void Task_Report(void);

#endif /* ASW_500MS_H */
