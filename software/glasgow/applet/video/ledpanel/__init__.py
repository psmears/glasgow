# Ref: https://some.sort.of.reference
# Accession: G???

import asyncio

from amaranth import *
from amaranth.lib import io, cdc, wiring, stream
from amaranth.lib.wiring import In, Out

from glasgow.support import logging
from glasgow.applet import GlasgowAppletV2


# FPGA commands
DO_SOMETHING      = 0b0001
DO_SOMETHING_ELSE = 0b0010

class LEDPanelInterface:
    def __init__(self, logger: logging.Logger, assembly: AbstractAssembly, *,
                 led_clk: GlasgowPin, led_strobe: GlasgowPin, oe: GlasgowPin, rgb1: GlasgowPin, rgb2: GlasgowPin, addr: GlasgowPin, rows: int, columns: int):
        self._logger = logger
        self._level = logging.DEBUG if self._logger.name == __name__ else logging.TRACE
        ports = assembly.add_port_group(
            led_clk=led_clk,
            led_strobe=led_strobe,
            oe=oe,
            addr=addr,
            rgb1=rgb1,
            rgb2=rgb2,
        )
        component = assembly.add_submodule(
            LEDPanelComponent(
                ports=ports,
                rows=rows,
                columns=columns,
                sys_clk_period=assembly.sys_clk_period
            )
        )
        self._pipe = assembly.add_inout_pipe(component.o_stream, component.i_stream)
        

class LEDPanelComponent(wiring.Component):
    i_stream: In(stream.Signature(8))
    o_stream: Out(stream.Signature(8))

    def __init__(self, ports, rows, columns, sys_clk_period):
        self._ports = ports
        self.rows = rows
        self.columns = columns
        self.sys_clk_period = sys_clk_period

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        led_clk = Signal(1)
        led_strobe = Signal(1)
        oe = Signal(1)
        rgb1 = Signal(3)
        rgb2 = Signal(3)
        addr = Signal(4)

        m.submodules.led_clk_buffer = led_clk_buffer = io.Buffer("o", self._ports.led_clk)
        m.submodules.led_strobe_buffer = led_strobe_buffer = io.Buffer("o", self._ports.led_strobe)
        m.submodules.oe_buffer = oe_buffer = io.Buffer("o", self._ports.oe)
        
        m.submodules.rgb1_buffer = rgb1_buffer = io.Buffer("o", self._ports.rgb1)
        m.submodules.rgb2_buffer = rgb2_buffer = io.Buffer("o", self._ports.rgb2)
        m.submodules.addr_buffer = addr_buffer = io.Buffer("o", self._ports.addr)

        m.d.comb += led_clk_buffer.oe.eq(1)
        m.d.comb += led_strobe_buffer.oe.eq(1)
        m.d.comb += oe_buffer.oe.eq(1)
        m.d.comb += rgb1_buffer.oe.eq(1)
        m.d.comb += rgb2_buffer.oe.eq(1)
        m.d.comb += addr_buffer.oe.eq(1)

        m.d.comb += led_clk_buffer.o.eq(led_clk)
        m.d.comb += led_strobe_buffer.o.eq(led_strobe)
        m.d.comb += oe_buffer.o.eq(oe)
        m.d.comb += rgb1_buffer.o.eq(rgb1)
        m.d.comb += rgb2_buffer.o.eq(rgb2)
        m.d.comb += addr_buffer.o.eq(addr)
        

        

        row = Signal(range(self.rows))
        col = Signal(range(self.columns))

        with m.FSM(init="NEWROW"):
            with m.State("NEWROW"):
                # Blank output before updating addresses, to help avoid ghosting
                m.d.sync += oe.eq(0)
                m.d.sync += led_strobe.eq(0)
                m.next = "SETADDR"
            with m.State("SETADDR"):
                m.d.sync += addr.eq(row)
                m.d.sync += col.eq(0)
                m.next = "SENDDATA"
            with m.State("SENDDATA"):
                # Assume data is accepted on falling edge of led_clk
                m.d.sync += rgb1.eq(0b111) # Need actual data
                m.d.sync += rgb2.eq(0b000) # Need actual data
                m.d.sync += led_clk.eq(1)
                m.next = "DATACLOCK"
            with m.State("DATACLOCK"):
                m.d.sync += led_clk.eq(0)
                m.d.sync += col.eq(col+1)
                with m.If(col == (self.columns - 1)):
                    m.next = "ENDROW"
                with m.Else():
                    m.next = "SENDDATA"
            with m.State("ENDROW"):
                m.d.sync += oe.eq(1)
                m.d.sync += led_strobe.eq(1)
                m.d.sync += row.eq(row+1)
                m.next = "NEWROW"
        

        return m


class DisplayLEDPanelApplet(GlasgowAppletV2):
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
        access.add_voltage_argument(parser)
        access.add_pins_argument(parser, "led_clk", default=True)
        access.add_pins_argument(parser, "led_strobe", default=True)
        access.add_pins_argument(parser, "oe", default=True)
        access.add_pins_argument(parser, "rgb1", width=3, default=True)
        access.add_pins_argument(parser, "rgb2", width=3, default=True)
        access.add_pins_argument(parser, "addr", width=4, default=True)
        parser.add_argument("--rows", help="Number of rows in display", type=int, default=32)
        parser.add_argument("--columns", help="Number of columns in display", type=int, default=32)

    def build(self, args):
        with self.assembly.add_applet(self):
            self.assembly.use_voltage(args.voltage)
            self.ledpanel_interface = LEDPanelInterface(self.logger,
                                                        self.assembly,
                                                        led_clk=args.led_clk,
                                                        led_strobe=args.led_strobe,
                                                        oe=args.oe,
                                                        rgb1=args.rgb1,
                                                        rgb2=args.rgb2,
                                                        addr=args.addr,
                                                        rows=args.rows,
                                                        columns=args.columns)

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
