/*
 * File: appKnbSwt.c
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

#include "appKnbSwt.h"
#include "rtwtypes.h"
#include "appKnbSwt_Intfc.h"
#include "appKnbSwt_Param.h"
#ifndef UCHAR_MAX
#include <limits.h>
#endif

#if ( UCHAR_MAX != (0xFFU) ) || ( SCHAR_MAX != (0x7F) )
#error Code was generated for compiler with different sized uchar/char. \
Consider adjusting Test hardware word size settings on the \
Hardware Implementation pane to match your compiler word sizes as \
defined in limits.h of the compiler. Alternatively, you can \
select the Test hardware is the same as production hardware option and \
select the Enable portable word sizes option on the Code Generation > \
Verification pane for ERT based targets, which will disable the \
preprocessor word size checks.
#endif

#if ( USHRT_MAX != (0xFFFFU) ) || ( SHRT_MAX != (0x7FFF) )
#error Code was generated for compiler with different sized ushort/short. \
Consider adjusting Test hardware word size settings on the \
Hardware Implementation pane to match your compiler word sizes as \
defined in limits.h of the compiler. Alternatively, you can \
select the Test hardware is the same as production hardware option and \
select the Enable portable word sizes option on the Code Generation > \
Verification pane for ERT based targets, which will disable the \
preprocessor word size checks.
#endif

#if ( UINT_MAX != (0xFFFFU) ) || ( INT_MAX != (0x7FFF) )
#error Code was generated for compiler with different sized uint/int. \
Consider adjusting Test hardware word size settings on the \
Hardware Implementation pane to match your compiler word sizes as \
defined in limits.h of the compiler. Alternatively, you can \
select the Test hardware is the same as production hardware option and \
select the Enable portable word sizes option on the Code Generation > \
Verification pane for ERT based targets, which will disable the \
preprocessor word size checks.
#endif

#if ( ULONG_MAX != (0xFFFFFFFFUL) ) || ( LONG_MAX != (0x7FFFFFFFL) )
#error Code was generated for compiler with different sized ulong/long. \
Consider adjusting Test hardware word size settings on the \
Hardware Implementation pane to match your compiler word sizes as \
defined in limits.h of the compiler. Alternatively, you can \
select the Test hardware is the same as production hardware option and \
select the Enable portable word sizes option on the Code Generation > \
Verification pane for ERT based targets, which will disable the \
preprocessor word size checks.
#endif

/* Real-time model */
static RT_MODEL rtM_;
RT_MODEL *const rtM = &rtM_;

/* Model step function */
void appKnbSwt_Runnable(void)
{
  uint32_T tmp;
  uint16_T rtb_Product;
  uint16_T tmp_0;

  /* Saturate: '<Root>/Saturation' incorporates:
   *  Inport: '<Root>/IN_KnbVal_Z'
   *  Product: '<Root>/Divide'
   */
  if (IN_KnbVal_Z <= 1023U) {
    tmp_0 = IN_KnbVal_Z;
  } else {
    tmp_0 = 1023U;
  }

  /* Product: '<Root>/Divide' incorporates:
   *  Constant: '<Root>/ADC_MAX'
   *  Saturate: '<Root>/Saturation'
   */
  tmp = tmp_0 * 1023UL / ADC_MAX;
  if (tmp > 65535UL) {
    tmp = 65535UL;
  }

  /* Product: '<Root>/Product' incorporates:
   *  Constant: '<Root>/PCT_MAX'
   *  Product: '<Root>/Divide'
   */
  rtb_Product = (uint16_T)(tmp * ((uint8_T)PCT_MAX) / 1023UL);

  /* Sum: '<Root>/Sum' incorporates:
   *  Constant: '<Root>/Knb_Hyst_Pc_Pt'
   *  Constant: '<Root>/Knb_Thresh_Pc_Pt'
   */
  tmp_0 = (uint16_T)Knb_Thresh_Pc_Pt + Knb_Hyst_Pc_Pt;
  if (tmp_0 > 255U) {
    tmp_0 = 255U;
  }

  /* Logic: '<Root>/OR' incorporates:
   *  Constant: '<Root>/Knb_Thresh_Pc_Pt'
   *  Logic: '<Root>/AND'
   *  RelationalOperator: '<Root>/LessThan'
   *  RelationalOperator: '<Root>/LessThan1'
   *  Sum: '<Root>/Sum'
   *  UnitDelay: '<Root>/Unit Delay'
   */
  OUT_Led1_B = ((rtb_Product < Knb_Thresh_Pc_Pt) || ((rtb_Product < tmp_0) &&
    OUT_Led1_B));
}

/* Model initialize function */
void appKnbSwt_initialize(void)
{
  /* (no initialization code required) */
}

/*
 * File trailer for generated code.
 *
 * [EOF]
 */
