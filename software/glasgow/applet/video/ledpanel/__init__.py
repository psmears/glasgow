# Ref: https://some.sort.of.reference
# Accession: G???

import asyncio

from amaranth import *
from amaranth.lib import io, cdc

from glasgow.support import logging
from glasgow.applet import *


# FPGA commands
DO_SOMETHING      = 0b0001
DO_SOMETHING_ELSE = 0b0010

class LEDPanelSubtarget(Elaboratable):
    def __init__(self, ports, out_fifo, in_fifo, rows, columns, sys_clk_freq):
        self.ports = ports
        self.out_fifo = out_fifo
        self.in_fifo = in_fifo
        self.sys_clk_freq = sys_clk_freq

    def elaborate(self, platform):
        m = Module()

        di = Signal(4)

        m.submodules.rs_buffer = rs_buffer = io.Buffer("o", self.ports.rs)
        m.submodules.rw_buffer = rw_buffer = io.Buffer("o", self.ports.rw)
        m.submodules.e_buffer = e_buffer = io.Buffer("o", self.ports.e)
        m.submodules.d_buffer = d_buffer = io.Buffer("io", self.ports.d)
        m.d.comb += d_buffer.oe.eq(~rw_buffer.o)
        m.submodules += cdc.FFSynchronizer(d_buffer.i, di)

        rx_setup_cyc = math.ceil(60e-9 * self.sys_clk_freq)
        e_pulse_cyc  = math.ceil(500e-9 * self.sys_clk_freq)
        e_wait_cyc   = math.ceil(700e-9 * self.sys_clk_freq)
        cmd_wait_cyc = math.ceil(1.52e-3 * self.sys_clk_freq)
        timer        = Signal(range(max([rx_setup_cyc, e_pulse_cyc, e_wait_cyc, cmd_wait_cyc])))

        cmd  = Signal(8)
        rdata = Signal(8)
        wdata = Signal(8)
        msb  = Signal()

        with m.FSM() as fsm:
            with m.State("IDLE"):
                m.d.sync += e_buffer.o.eq(0)
                m.d.comb += self.out_fifo.r_en.eq(1)
                with m.If(self.out_fifo.r_rdy):
                    m.d.sync += cmd.eq(self.out_fifo.r_data)
                    m.next = "COMMAND"

            with m.State("COMMAND"):
                m.d.sync += [
                    msb.eq((cmd & XFER_BIT_HALF) == 0),
                    rs_buffer.o.eq((cmd & XFER_BIT_DATA) != 0),
                    rw_buffer.o.eq((cmd & XFER_BIT_READ) != 0),
                ]
                with m.If(cmd & XFER_BIT_WAIT):
                    m.d.sync += timer.eq(cmd_wait_cyc)
                    m.next = "WAIT"
                with m.Elif(cmd & XFER_BIT_READ):
                    m.d.sync += timer.eq(rx_setup_cyc)
                    m.next = "READ-SETUP"
                with m.Else():
                    m.d.comb += self.out_fifo.r_en.eq(1)
                    with m.If(self.out_fifo.r_rdy):
                        m.d.sync += wdata.eq(self.out_fifo.r_data)
                        m.next = "WRITE"

            with m.State("WRITE"):
                with m.If(timer == 0):
                    m.d.sync += [
                        e_buffer.o.eq(1),
                        d_buffer.o.eq(Mux(msb, wdata[4:], wdata[:4])),
                        timer.eq(e_pulse_cyc),
                    ]
                    m.next = "WRITE-HOLD"
                with m.Else():
                    m.d.sync += timer.eq(timer - 1)

            with m.State("WRITE-HOLD"):
                with m.If(timer == 0):
                    m.d.sync += [
                        e_buffer.o.eq(0),
                        msb.eq(0),
                        timer.eq(e_wait_cyc),
                    ]
                    with m.If(msb):
                        m.next = "WRITE"
                    with m.Else():
                        m.next = "WAIT"
                with m.Else():
                    m.d.sync += timer.eq(timer - 1)

            with m.State("READ-SETUP"):
                with m.If(timer == 0):
                    m.d.sync += [
                        e_buffer.o.eq(1),
                        timer.eq(e_pulse_cyc),
                    ]
                    m.next = "READ"
                with m.Else():
                    m.d.sync += timer.eq(timer - 1)

            with m.State("READ"):
                with m.If(timer == 0):
                    with m.If(((cmd & XFER_BIT_DATA) == 0) & msb & di[3]):
                        # BF=1, wait until it goes low
                        pass
                    with m.Else():
                        m.d.sync += [
                            e_buffer.o.eq(0),
                            msb.eq(0),
                            timer.eq(e_wait_cyc),
                        ]
                        with m.If(msb):
                            m.d.sync += rdata[4:].eq(di)
                            m.next = "READ-SETUP"
                        with m.Else():
                            m.d.sync += rdata[:4].eq(di)
                            m.next = "READ-PROCESS"
                with m.Else():
                    m.d.sync += timer.eq(timer - 1)

            with m.State("READ-PROCESS"):
                with m.If(cmd & XFER_BIT_DATA):
                    m.d.comb += [
                        self.in_fifo.w_data.eq(rdata),
                        self.in_fifo.w_en.eq(1),
                    ]
                    with m.If(self.in_fifo.w_rdy):
                        m.next = "WAIT"
                with m.Else():
                    # done reading status register, ignore it and continue
                    m.next = "WAIT"

            with m.State("WAIT"):
                with m.If(timer == 0):
                    m.next = "IDLE"
                with m.Else():
                    m.d.sync += timer.eq(timer - 1)

        return m


class DisplayLEDPanelApplet(GlasgowApplet):
    preview = True
    logger = logging.getLogger(__name__)
    help = "display stuff on 32x32 LED panels (and similar)"
    description = """
    Control 32z32 LED panels that send the data serially

    Port pins should be connected to display pins as follows: [...]. Port voltage is set to 5.0 V.
    """
    # The revA/B level shifters interact very badly with the input cascade of most such displays,
    # causing severe glitching.
    required_revision = "C0"

    @classmethod
    def add_build_arguments(cls, parser, access):
        access.add_build_arguments(parser)
        access.add_pins_argument(parser, "led_clk", default=True)
        access.add_pins_argument(parser, "led_strobe", default=True)
        access.add_pins_argument(parser, "oe", default=True)
        access.add_pins_argument(parser, "rgb1", width=3, default=True)
        access.add_pins_argument(parser, "rgb2", width=3, default=True)
        access.add_pins_argument(parser, "addr", width=4, default=True)
        parser.add_argument("--rows", help="Number of rows in display", type=int)
        parser.add_argument("--columns", help="Number of columns in display", type=int)

    def build(self, target, args):
        self.mux_interface = iface = target.multiplexer.claim_interface(self, args)
        iface.add_subtarget(LEDPanelSubtarget(
            ports=iface.get_port_group(
                led_clk=args.led_clk,
                led_strobe=args.led_strobe,
                oe=args.oe,
                rgb1=args.rgb1,
                rgb2=args.rgb2,
            ),
            rows=args.rows,
            columns=args.columns,
            out_fifo=iface.get_out_fifo(),
            in_fifo=iface.get_in_fifo(),
            sys_clk_freq=target.sys_clk_freq,
        ))

    @classmethod
    def add_run_arguments(cls, parser, access):
        parser.add_argument(
            "--reset", default=False, action="store_true",
            help="power-cycle the port on startup")

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args=None)

        if args.reset:
            await device.set_voltage("AB", 0.0)
            await asyncio.sleep(0.3)
        await device.set_voltage("AB", 5.0)
        await asyncio.sleep(0.040) # wait 40ms after reset

        # TODO: abstract this away into a HD44780Interface
        async def init(command, poll):
            await iface.write([XFER_INIT, command, XFER_POLL if poll else XFER_WAIT])

        async def cmd(command):
            await iface.write([XFER_COMMAND, command, XFER_POLL])

        async def data(bytes):
            for byte in bytes:
                await iface.write([XFER_WRITE, byte, XFER_POLL])

        # HD44780 may be in either 4-bit or 8-bit mode and we don't know which.
        # The following sequence brings it to 4-bit mode regardless of which one it was in.
        await init(0x03, poll=False) # either CMD_FUNCTION_SET|BIT_IFACE_8BIT or CMD_CURSOR_HOME
                                     # or the second nibble of an unknown command/data
        await init(0x03, poll=False) # either CMD_FUNCTION_SET|BIT_IFACE_8BIT or CMD_CURSOR_HOME
                                     # or the second nibble of CMD_FUNCTION_SET (the set bits
                                     # are ignored)
        await init(0x03, poll=False) # CMD_FUNCTION_SET|BIT_IFACE_8BIT
        await init(0x02, poll=True)  # CMD_FUNCTION_SET

        await cmd(CMD_FUNCTION_SET|BIT_DISPLAY_2_LINE)
        await cmd(CMD_DISPLAY_ON_OFF|BIT_DISPLAY_ON|BIT_CURSOR_BLINK)
        await cmd(CMD_CLEAR_DISPLAY)
        await cmd(CMD_ENTRY_MODE|BIT_CURSOR_INC_POS)
        await data(b"Hello")
        await cmd(CMD_DDRAM_ADDRESS|0x40)
        await data(b"  World")
        await iface.flush()
        await asyncio.sleep(1)

        from datetime import datetime
        while True:
            await asyncio.sleep(1)
            await cmd(CMD_DDRAM_ADDRESS|0x00)
            await data(datetime.now().strftime("%H:%M:%S").encode("ascii"))
            await cmd(CMD_DDRAM_ADDRESS|0x40)
            await data(datetime.now().strftime("%y-%m-%d").encode("ascii"))
            await iface.flush()

    # @classmethod
    # def tests(cls):
    #     from . import test
    #     return test.DisplayHD44780AppletTestCase
