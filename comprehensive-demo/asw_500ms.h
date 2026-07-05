/**
 * @file    asw_500ms.h
 * @brief   500 ms rate - heartbeat LED + periodic status report
 *          (TASK_STATUS).
 */

#ifndef ASW_500MS_H
#define ASW_500MS_H

/** 500 ms task entry (released by ALARM_STATUS, WCET <= 2 ms). */
extern void Task_Status(void);

#endif /* ASW_500MS_H */
