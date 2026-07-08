/**
 * @file    usb_cdc.c
 * @brief   Native USB CDC-ACM console for the ATmega32U4 (see usb_cdc.h).
 *
 * Minimal single-port USB 2.0 CDC-ACM device: PLL + controller bring-up,
 * endpoint-0 enumeration (standard + CDC class requests), and a bulk IN/OUT
 * virtual COM port bridged to the same non-blocking ring buffers the USART
 * `uart.c` uses - so it exports the identical `Uart_*` console API.
 *
 * Endpoint map (device -> host = IN):
 *   EP0  control        64 B
 *   EP2  interrupt IN   16 B   CDC notification (unused, present for the class)
 *   EP3  bulk OUT       64 B   host -> device  (RX ring)
 *   EP4  bulk IN        64 B   device -> host  (TX ring)
 *
 * TX/RX are serviced from the 1 ms Start-Of-Frame interrupt, so a caller never
 * blocks on the host. Register/bit names follow the ATmega32U4 datasheet.
 *
 * The whole file compiles to nothing on parts without an on-chip USB controller
 * (no USBCON), so it is safe in the all-MCU driver compile gate.
 */
#include <avr/io.h>

#if defined(USBCON)

#include <avr/interrupt.h>
#include <avr/pgmspace.h>
#include <util/atomic.h>

#include "usb_cdc.h"

/* ------------------------------------------------------------------ */
/* Endpoint geometry                                                   */
/* ------------------------------------------------------------------ */
#define CDC_ACM_EP      2u      /* interrupt IN (notifications)  */
#define CDC_RX_EP       3u      /* bulk OUT (host -> device)     */
#define CDC_TX_EP       4u      /* bulk IN  (device -> host)     */
#define CDC_ACM_SIZE    16u
#define CDC_RX_SIZE     64u
#define CDC_TX_SIZE_EP  64u
#define EP0_SIZE        64u

/* UECFG1X size field encodings (EPSIZE bits 6:4): 16->0b001, 64->0b011. */
#define EPSZ_16         ((uint8_t)(1u << 4))
#define EPSZ_64         ((uint8_t)(3u << 4))
#define EP_SINGLE_BANK  ((uint8_t)((0u << 2) | (1u << 1)))  /* EPBK0 + ALLOC */
#define EP_DOUBLE_BANK  ((uint8_t)((1u << 2) | (1u << 1)))  /* EPBK1 + ALLOC */

/* ------------------------------------------------------------------ */
/* Application rings (overridable via -D from app.yaml, like uart.c)   */
/* ------------------------------------------------------------------ */
#ifndef UART_TX_SIZE
#define UART_TX_SIZE   128u
#endif
#ifndef UART_RX_SIZE
#define UART_RX_SIZE   64u
#endif
#if (UART_TX_SIZE & (UART_TX_SIZE - 1u)) != 0u || (UART_TX_SIZE > 256u)
#error "UART_TX_SIZE must be a power of two, 2..256"
#endif
#if (UART_RX_SIZE & (UART_RX_SIZE - 1u)) != 0u || (UART_RX_SIZE > 256u)
#error "UART_RX_SIZE must be a power of two, 2..256"
#endif
#define TX_MASK        (UART_TX_SIZE - 1u)
#define RX_MASK        (UART_RX_SIZE - 1u)

static volatile uint8_t txBuf[UART_TX_SIZE];
static volatile uint8_t txHead;
static volatile uint8_t txTail;
static volatile uint8_t txDropped;
static volatile uint8_t rxBuf[UART_RX_SIZE];
static volatile uint8_t rxHead;
static volatile uint8_t rxTail;

static volatile uint8_t usbConfig;   /* current SET_CONFIGURATION value (0 = not) */

/* CDC line coding: 9600 8N1. Stored so GET_LINE_CODING echoes SET_LINE_CODING. */
static uint8_t lineCoding[7] = {0x80u, 0x25u, 0x00u, 0x00u, 0x00u, 0x00u, 0x08u};

/* ================================================================== */
/* USB descriptors (PROGMEM)                                           */
/* ================================================================== */
static const uint8_t PROGMEM deviceDescriptor[] = {
    18,        /* bLength                        */
    1,         /* bDescriptorType = DEVICE       */
    0x00, 0x02,/* bcdUSB 2.0                     */
    2,         /* bDeviceClass = CDC             */
    0, 0,      /* subclass, protocol             */
    EP0_SIZE,  /* bMaxPacketSize0                */
    0x41, 0x23,/* idVendor  0x2341 (Arduino)     */
    0x36, 0x80,/* idProduct 0x8036 (Leonardo)    */
    0x00, 0x01,/* bcdDevice                      */
    1, 2, 0,   /* iManufacturer, iProduct, iSerial */
    1          /* bNumConfigurations             */
};

#define CONFIG_TOTAL 62
static const uint8_t PROGMEM configDescriptor[] = {
    /* Configuration */
    9, 2, CONFIG_TOTAL, 0, 2, 1, 0, 0xC0, 50,
    /* Interface 0: Communications (ACM) */
    9, 4, 0, 0, 1, 0x02, 0x02, 0x01, 0,
    /* CDC Header functional */
    5, 0x24, 0x00, 0x10, 0x01,
    /* CDC Call Management functional */
    5, 0x24, 0x01, 0x01, 1,
    /* CDC ACM functional */
    4, 0x24, 0x02, 0x06,
    /* CDC Union functional */
    5, 0x24, 0x06, 0, 1,
    /* Endpoint: interrupt IN (notification) */
    7, 5, (0x80u | CDC_ACM_EP), 0x03, CDC_ACM_SIZE, 0, 64,
    /* Interface 1: Data */
    9, 4, 1, 0, 2, 0x0A, 0x00, 0x00, 0,
    /* Endpoint: bulk OUT */
    7, 5, CDC_RX_EP, 0x02, CDC_RX_SIZE, 0, 0,
    /* Endpoint: bulk IN */
    7, 5, (0x80u | CDC_TX_EP), 0x02, CDC_TX_SIZE_EP, 0, 0
};

static const uint8_t PROGMEM string0[] = {4, 3, 0x09, 0x04};  /* LANGID en-US */
static const uint8_t PROGMEM string1[] = {                    /* "EROS" */
    10, 3, 'E', 0, 'R', 0, 'O', 0, 'S', 0};
static const uint8_t PROGMEM string2[] = {                    /* "EROS Console" */
    26, 3, 'E', 0, 'R', 0, 'O', 0, 'S', 0, ' ', 0, 'C', 0,
    'o', 0, 'n', 0, 's', 0, 'o', 0, 'l', 0, 'e', 0};

/* ------------------------------------------------------------------ */
/* Endpoint helpers                                                    */
/* ------------------------------------------------------------------ */
static void EpSelect(uint8_t ep)
{
    UENUM = ep;
}

static void EpConfigure(uint8_t ep, uint8_t cfg0, uint8_t cfg1)
{
    EpSelect(ep);
    UECONX = (uint8_t)(1u << EPEN);
    UECFG0X = cfg0;
    UECFG1X = cfg1;
}

/* Send `len` bytes from PROGMEM over EP0, honouring the host's wLength and the
 * control-transfer bank handshake (wait TXINI, fill <=EP0_SIZE, clear TXINI). */
static void Ep0SendProgmem(const uint8_t *data, uint8_t len, uint16_t wLength)
{
    uint8_t remaining = (len < wLength) ? len : (uint8_t)wLength;

    do
    {
        /* Abort if the host sends an OUT (status stage) early. */
        while ((UEINTX & (uint8_t)((1u << TXINI) | (1u << RXOUTI))) == 0u)
        {
        }
        if ((UEINTX & (uint8_t)(1u << RXOUTI)) != 0u)
        {
            return;                       /* host aborted the IN */
        }
        uint8_t n = (remaining < EP0_SIZE) ? remaining : (uint8_t)EP0_SIZE;
        uint8_t i;
        for (i = 0u; i < n; i++)
        {
            UEDATX = pgm_read_byte(data++);
        }
        remaining = (uint8_t)(remaining - n);
        UEINTX = (uint8_t)~(1u << TXINI);  /* send the bank */
    } while (remaining != 0u);
}

/* ================================================================== */
/* Enumeration: standard + CDC control requests on EP0                 */
/* ================================================================== */
static void Ep0Setup(void)
{
    uint8_t bmRequestType = UEDATX;
    uint8_t bRequest      = UEDATX;
    uint8_t wValueL       = UEDATX;
    uint8_t wValueH       = UEDATX;
    (void)UEDATX;                          /* wIndexL */
    (void)UEDATX;                          /* wIndexH */
    uint16_t wLength      = UEDATX;
    wLength |= (uint16_t)((uint16_t)UEDATX << 8);

    UEINTX = (uint8_t)~(1u << RXSTPI);     /* ACK the SETUP packet */

    if (bRequest == 6u && bmRequestType == 0x80u)   /* GET_DESCRIPTOR */
    {
        const uint8_t *p = 0;
        uint8_t len = 0u;
        if (wValueH == 1u)                 /* DEVICE */
        {
            p = deviceDescriptor;
            len = (uint8_t)sizeof(deviceDescriptor);
        }
        else if (wValueH == 2u)            /* CONFIGURATION */
        {
            p = configDescriptor;
            len = (uint8_t)sizeof(configDescriptor);
        }
        else if (wValueH == 3u)            /* STRING */
        {
            if (wValueL == 0u) { p = string0; len = (uint8_t)sizeof(string0); }
            else if (wValueL == 1u) { p = string1; len = (uint8_t)sizeof(string1); }
            else if (wValueL == 2u) { p = string2; len = (uint8_t)sizeof(string2); }
        }
        if (p != 0)
        {
            Ep0SendProgmem(p, len, wLength);
        }
        else
        {
            UECONX = (uint8_t)((1u << EPEN) | (1u << STALLRQ));  /* unknown */
        }
        return;
    }

    if (bRequest == 5u && bmRequestType == 0x00u)   /* SET_ADDRESS */
    {
        UEINTX = (uint8_t)~(1u << TXINI);  /* zero-length status IN */
        while ((UEINTX & (uint8_t)(1u << TXINI)) == 0u)
        {
        }
        UDADDR = (uint8_t)(wValueL | (1u << ADDEN));
        return;
    }

    if (bRequest == 9u && bmRequestType == 0x00u)   /* SET_CONFIGURATION */
    {
        usbConfig = wValueL;
        UEINTX = (uint8_t)~(1u << TXINI);  /* status stage */
        /* Bring up the CDC endpoints. */
        EpConfigure(CDC_ACM_EP, (uint8_t)((0x03u << 6) | (1u << 0)),
                    (uint8_t)(EPSZ_16 | EP_SINGLE_BANK));  /* interrupt IN  */
        EpConfigure(CDC_RX_EP, (uint8_t)(0x02u << 6),
                    (uint8_t)(EPSZ_64 | EP_DOUBLE_BANK));  /* bulk OUT      */
        EpConfigure(CDC_TX_EP, (uint8_t)((0x02u << 6) | (1u << 0)),
                    (uint8_t)(EPSZ_64 | EP_DOUBLE_BANK));  /* bulk IN       */
        return;
    }

    if (bRequest == 8u && bmRequestType == 0x80u)   /* GET_CONFIGURATION */
    {
        while ((UEINTX & (uint8_t)(1u << TXINI)) == 0u)
        {
        }
        UEDATX = usbConfig;
        UEINTX = (uint8_t)~(1u << TXINI);
        return;
    }

    if (bRequest == 0u && (bmRequestType & 0x80u) != 0u)  /* GET_STATUS */
    {
        while ((UEINTX & (uint8_t)(1u << TXINI)) == 0u)
        {
        }
        UEDATX = 0u;
        UEDATX = 0u;
        UEINTX = (uint8_t)~(1u << TXINI);
        return;
    }

    /* CDC class requests (bmRequestType 0x21 host->dev, 0xA1 dev->host). */
    if (bRequest == 0x20u && bmRequestType == 0x21u)      /* SET_LINE_CODING */
    {
        while ((UEINTX & (uint8_t)(1u << RXOUTI)) == 0u)
        {
        }
        uint8_t i;
        for (i = 0u; i < 7u; i++)
        {
            lineCoding[i] = UEDATX;
        }
        UEINTX = (uint8_t)~(1u << RXOUTI);
        UEINTX = (uint8_t)~(1u << TXINI);  /* status */
        return;
    }
    if (bRequest == 0x21u && bmRequestType == 0xA1u)      /* GET_LINE_CODING */
    {
        while ((UEINTX & (uint8_t)(1u << TXINI)) == 0u)
        {
        }
        uint8_t i;
        for (i = 0u; i < 7u; i++)
        {
            UEDATX = lineCoding[i];
        }
        UEINTX = (uint8_t)~(1u << TXINI);
        return;
    }
    if (bRequest == 0x22u && bmRequestType == 0x21u)      /* SET_CONTROL_LINE_STATE */
    {
        UEINTX = (uint8_t)~(1u << TXINI);  /* just ACK */
        return;
    }

    /* Anything unhandled: stall. */
    UECONX = (uint8_t)((1u << EPEN) | (1u << STALLRQ));
}

/* Move queued TX bytes into the bulk-IN bank and RX bytes out of the bulk-OUT
 * bank. Called from the 1 ms SOF ISR - bounded, non-blocking. */
static void CdcService(void)
{
    /* TX: fill the IN endpoint while the bank accepts bytes and the ring has data. */
    EpSelect(CDC_TX_EP);
    if ((UEINTX & (uint8_t)(1u << RWAL)) != 0u)
    {
        uint8_t wrote = 0u;

        while (txTail != txHead)
        {
            if ((UEINTX & (uint8_t)(1u << RWAL)) == 0u)
            {
                break;                     /* bank full */
            }
            UEDATX = txBuf[txTail];
            txTail = (uint8_t)((txTail + 1u) & TX_MASK);
            wrote  = 1u;
        }
        if (wrote != 0u)
        {
            /* Release the bank (send). FIFOCON=0, NAKINI cleared. Releasing
             * an untouched bank would queue a pointless zero-length packet
             * every SOF (1 ms) while the console idles. */
            UEINTX = (uint8_t)~((1u << FIFOCON) | (1u << TXINI));
        }
    }

    /* RX: drain the OUT endpoint bank into the ring. */
    EpSelect(CDC_RX_EP);
    if ((UEINTX & (uint8_t)(1u << RXOUTI)) != 0u)
    {
        while ((UEINTX & (uint8_t)(1u << RWAL)) != 0u)
        {
            const uint8_t data = UEDATX;
            const uint8_t next = (uint8_t)((rxHead + 1u) & RX_MASK);
            if (next != rxTail)
            {
                rxBuf[rxHead] = data;
                rxHead = next;
            }
        }
        UEINTX = (uint8_t)~((1u << FIFOCON) | (1u << RXOUTI));
    }
}

/* Device-level events: end-of-reset (reconfigure EP0) + SOF (service data). */
ISR(USB_GEN_vect) /* Category 1: no OS service calls */
{
    const uint8_t d = UDINT;
    UDINT = 0u;

    if ((d & (uint8_t)(1u << EORSTI)) != 0u)
    {
        EpConfigure(0u, (uint8_t)(0x00u << 6),
                    (uint8_t)(EPSZ_64 | EP_SINGLE_BANK));  /* EP0 control */
        UEIENX = (uint8_t)(1u << RXSTPE);                  /* SETUP interrupt */
        usbConfig = 0u;
    }
    if ((d & (uint8_t)(1u << SOFI)) != 0u && usbConfig != 0u)
    {
        CdcService();
    }
}

/* Endpoint communication: EP0 SETUP packets during enumeration. */
ISR(USB_COM_vect) /* Category 1: no OS service calls */
{
    EpSelect(0u);
    if ((UEINTX & (uint8_t)(1u << RXSTPI)) != 0u)
    {
        Ep0Setup();
    }
}

/* ================================================================== */
/* Public console API (drop-in for uart.c)                             */
/* ================================================================== */
void Uart_Init(void)
{
    UHWCON = (uint8_t)(1u << UVREGE);           /* enable USB pad regulator */
    USBCON = (uint8_t)((1u << USBE) | (1u << FRZCLK));

    /* 48 MHz USB clock from the PLL (16 MHz input -> PINDIV). */
    PLLCSR = (uint8_t)((1u << PINDIV) | (1u << PLLE));
    while ((PLLCSR & (uint8_t)(1u << PLOCK)) == 0u)
    {
    }

    USBCON = (uint8_t)((1u << USBE) | (1u << OTGPADE));  /* unfreeze + VBUS pad */
    UDCON = 0u;                                  /* attach (clear DETACH) */
    usbConfig = 0u;
    UDIEN = (uint8_t)((1u << EORSTE) | (1u << SOFE));    /* reset + SOF IRQs */
}

uint8_t Uart_PutChar(char c)
{
    uint8_t ok = 1u;
    const uint8_t next = (uint8_t)((txHead + 1u) & TX_MASK);

    if (next == txTail)
    {
        txDropped++;                              /* never block a task */
        ok = 0u;
    }
    else
    {
        txBuf[txHead] = (uint8_t)c;
        txHead = next;
    }
    return ok;
}

/* Uart_Print / Uart_Print_P / Uart_PrintU16 / Uart_PrintHex8 are the shared,
 * transport-independent formatters - they live once in tests/reference-demo/
 * uart_print.c (a usb_cdc console app links it alongside this file). */

uint8_t Uart_GetChar(char *c)
{
    uint8_t ok = 0u;

    if (rxTail != rxHead)
    {
        *c = (char)rxBuf[rxTail];
        rxTail = (uint8_t)((rxTail + 1u) & RX_MASK);
        ok = 1u;
    }
    return ok;
}

uint8_t Uart_TxDropped(void)
{
    return txDropped;
}

uint8_t Cdc_IsConfigured(void)
{
    return (usbConfig != 0u) ? 1u : 0u;
}

#endif /* defined(USBCON) */
