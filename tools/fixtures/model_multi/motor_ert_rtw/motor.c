/*
 * File: motor.c
 *
 * Synthetic second SWC for the multi-model fixture. A trivial runnable so the
 * exported globals and entry points exist; the fixture pins erosgen's
 * generation, it is not compiled in CI.
 */

#include "motor.h"
#include "motor_Intfc.h"

uint16_T IN_Speed_Rpm;
boolean_T OUT_Fan_B;

void motor_initialize(void)
{
    OUT_Fan_B = false;
}

void motor_Runnable(void)
{
    OUT_Fan_B = (boolean_T)(IN_Speed_Rpm > 512U);
}
