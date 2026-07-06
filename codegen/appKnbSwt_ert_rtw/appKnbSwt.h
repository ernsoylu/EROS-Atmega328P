/*
 * File: appKnbSwt.h
 *
 * Code generated for Simulink model 'appKnbSwt'.
 *
 * Model version                  : 1.15
 * Simulink Coder version         : 25.2 (R2025b) 28-Jul-2025
 * C/C++ source code generated on : Mon Jul  6 10:33:09 2026
 *
 * Target selection: ert.tlc
 * Embedded hardware selection: Atmel->AVR (8-bit)
 * Code generation objectives:
 *    1. RAM efficiency
 *    2. Execution efficiency
 * Validation result: Not run
 */

#ifndef appKnbSwt_h_
#define appKnbSwt_h_
#ifndef appKnbSwt_COMMON_INCLUDES_
#define appKnbSwt_COMMON_INCLUDES_
#include "rtwtypes.h"
#endif                                 /* appKnbSwt_COMMON_INCLUDES_ */

#include "appKnbSwt_types.h"

/* Includes for objects with custom storage classes */
#include "appKnbSwt_Param.h"
#include "appKnbSwt_Intfc.h"

/* Macros for accessing real-time model data structure */
#ifndef rtmGetErrorStatus
#define rtmGetErrorStatus(rtm)         ((rtm)->errorStatus)
#endif

#ifndef rtmSetErrorStatus
#define rtmSetErrorStatus(rtm, val)    ((rtm)->errorStatus = (val))
#endif

#define appKnbSwt_M                    (rtM)

/* Real-time Model Data Structure */
struct tag_RTM {
  const char_T * volatile errorStatus;
};

/* Model entry point functions */
extern void appKnbSwt_initialize(void);
extern void appKnbSwt_Runnable(void);

/* Real-time Model object */
extern RT_MODEL *const rtM;

/*-
 * The generated code includes comments that allow you to trace directly
 * back to the appropriate location in the model.  The basic format
 * is <system>/block_name, where system is the system number (uniquely
 * assigned by Simulink) and block_name is the name of the block.
 *
 * Use the MATLAB hilite_system command to trace the generated code back
 * to the model.  For example,
 *
 * hilite_system('<S3>')    - opens system 3
 * hilite_system('<S3>/Kp') - opens and selects block Kp which resides in S3
 *
 * Here is the system hierarchy for this model
 *
 * '<Root>' : 'appKnbSwt'
 */
#endif                                 /* appKnbSwt_h_ */

/*
 * File trailer for generated code.
 *
 * [EOF]
 */
