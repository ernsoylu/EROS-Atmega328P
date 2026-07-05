/**
 * @file    asw_50ms.h
 * @brief   50 ms rate - command parser over UART + button events
 *          (TASK_CMD).
 */

#ifndef ASW_50MS_H
#define ASW_50MS_H

/** 50 ms task entry (released by ALARM_CMD, WCET <= 2 ms). */
extern void Task_Cmd(void);

#endif /* ASW_50MS_H */
