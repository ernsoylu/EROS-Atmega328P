/**
 * @file    asw_50ms.h
 * @brief   50 ms rate - scope jitter channel + serial command parser
 *          and button events (TASK_CMD).
 */

#ifndef ASW_50MS_H
#define ASW_50MS_H

/** 50 ms task entry (released by ALARM_CMD). */
extern void Task_Cmd(void);

#endif /* ASW_50MS_H */
