/* ====================================================================== *
 * uart_regs.h - USART instance selection for uart.c.
 *
 * The 328P/2560 console is USART0 (its avr-libc vectors are the un-numbered
 * USART_*_vect on the single-USART 328P). The ATmega32U4 has NO USART0 at all -
 * only USART1 (D0/D1) - so a 32U4 app builds uart.c with -DUART_USART=1 (erosgen
 * emits it from the profile's `uart_instance`). The aliases below map the
 * register / bit / vector names for the selected instance so uart.c has one
 * code path; UART_USART == 0 expands to the exact USART0 names uart.c used
 * before, so the 328P object code is byte-identical.
 *
 * Only 0 (USART0, 328P spelling) and 1 (USART1) are supported - the two console
 * targets. A native USB CDC console for the 32U4's on-chip USB is deliberately
 * out of scope (a full device stack is disproportionate for this kernel; use an
 * external USB-serial adapter on USART1).
 * ====================================================================== */
#ifndef UART_REGS_H
#define UART_REGS_H

#ifndef UART_USART
#define UART_USART 0
#endif

#if UART_USART == 0
#define UART_UDR        UDR0
#define UART_UBRRH      UBRR0H
#define UART_UBRRL      UBRR0L
#define UART_UCSRA      UCSR0A
#define UART_UCSRB      UCSR0B
#define UART_UCSRC      UCSR0C
#define UART_RXEN       RXEN0
#define UART_TXEN       TXEN0
#define UART_RXCIE      RXCIE0
#define UART_UDRIE      UDRIE0
#define UART_UCSZ1      UCSZ01
#define UART_UCSZ0      UCSZ00
#define UART_UDRE_VECT  USART_UDRE_vect
#define UART_RX_VECT    USART_RX_vect

#elif UART_USART == 1
#define UART_UDR        UDR1
#define UART_UBRRH      UBRR1H
#define UART_UBRRL      UBRR1L
#define UART_UCSRA      UCSR1A
#define UART_UCSRB      UCSR1B
#define UART_UCSRC      UCSR1C
#define UART_RXEN       RXEN1
#define UART_TXEN       TXEN1
#define UART_RXCIE      RXCIE1
#define UART_UDRIE      UDRIE1
#define UART_UCSZ1      UCSZ11
#define UART_UCSZ0      UCSZ10
#define UART_UDRE_VECT  USART1_UDRE_vect
#define UART_RX_VECT    USART1_RX_vect

#else
#error "UART_USART must be 0 (USART0) or 1 (USART1)"
#endif

#endif /* UART_REGS_H */
