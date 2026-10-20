# Ref: https://some.sort.of.reference
# Accession: G???

import asyncio
import dataclasses

from amaranth import *
from amaranth.lib import io, cdc, wiring, stream, data, memory
from amaranth.lib.wiring import In, Out

from glasgow.support import logging
from glasgow.applet import GlasgowAppletV2


# FPGA commands
CMD_BLANK      = 0b0000
CMD_SHOW       = 0b0001
CMD_SET_SCREEN = 0b0010
CMD_SET_WRITE  = 0b0100
CMD_ORIGIN     = 0b0110
CMD_MOVE_UP    = 0b1000
CMD_MOVE_DOWN  = 0b1001
CMD_MOVE_LEFT  = 0b1010
CMD_MOVE_RIGHT = 0b1011

# Colour resolution in bits per pixel
BPP = 4
BPP_R = BPP
BPP_G = BPP
BPP_B = BPP

# Number of supported screen buffers
MAX_SCREENS = 2

# Memory layout
pixel_r = unsigned(BPP_R)
pixel_g = unsigned(BPP_G)
pixel_b = unsigned(BPP_B)
pixel_shape = data.StructLayout({
    "red": pixel_r,
    "green": pixel_g,
    "blue": pixel_b,
})

# Command layout
set_value_shape = data.StructLayout({
    "value": unsigned(BPP),
    "advance": unsigned(1),
    "set_r": unsigned(1),
    "set_g": unsigned(1),
    "set_b": unsigned(1),
})
command_shape = data.UnionLayout({
    "set_value": set_value_shape,
    "command": unsigned(5),
})


@dataclasses.dataclass
class LEDPanelDimensions:
    rows: int
    super_rows: int
    columns: int

    @property
    def addr_shape(self):
        return data.StructLayout({
            "column": Shape.cast(range(self.columns)),
            "row": Shape.cast(range(self.rows)),
            "screen": Shape.cast(range(MAX_SCREENS)),
        })

    def get_memory(self):
        return memory.Memory(
            shape=pixel_shape,
            depth=1<<self.addr_shape.size,
            init=[])


class LEDMemorySignature(wiring.Signature):
    def __init__(self, dimensions: LEDPanelDimensions):
        wr_port_sig = memory.WritePort.Signature(
            addr_width = 5, # @@@
            shape=pixel_shape,
        )
        super().__init__(wr_port_sig.members)



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
        
class LEDPanelController(wiring.Component):

    def __init__(self, dimensions: LEDPanelDimensions):
        self.dimensions = dimensions

        super().__init__({
            "i_stream": In(stream.Signature(8)),
            "wr_port": In(LEDMemorySignature(self.dimensions)),
        })

    def elaborate(self, platform):
        m = Module()

        # Don't write to the memory by default
        m.d.sync += wr_port.en.eq(0)

        screen_shape = Shape.cast(range(MAX_SCREENS))
        row_shape = Shape.cast(range(self.dimensions.rows))
        super_row_shape = Shape.cast(range(self.dimensions.super_rows))
        column_shape = Shape.cast(range(self.dimensions.columns))
        pixel_pos = data.StructLayout({
            "screen": screen_shape,
            "super_row": super_row_shape,
            "row": row_shape,
            "column": column_shape,
        })

        mem = Memory(shape=pixel, depth=1<<pixel_pos.width, init=[])

        writing_screen = Signal(screen_shape, init=0)
        writing_row = Signal(row_shape, init=0)
        writing_super_row = Signal(super_row_shape, init=0)
        writing_column = Signal(column_shape, init=0)
        current_pixel = Signal(pixel_shape, init={
            "red": 0,
            "green": 0,
            "blue": 0
        })

        m.d.comb += self.i_stream.ready.eq(1)
        command = Signal(command_shape)
        m.d.comb += command.eq(self.i_stream.payload)
        
        with m.If(i_stream.valid):
            with m.If(command.set_value.set_r |
                      command.set_value.set_g |
                      command.set_value.set_g):
                # This is a command to set the current pixel value, and
                # possibly write it to memory and move on
                next_pixel = Signal(pixel_shape)
                m.d.comb += next_pixel.eq({
                    "red": Mux(command.set_value.set_r, command.set_value.value, current_pixel.r),
                    "green": Mux(command.set_value.set_g, command.set_value.value, current_pixel.g),
                    "blue": Mux(command.set_value.set_b, command.set_value.value, current_pixel.b),
                })
                m.d.sync += current_pixel.eq(next_pixel)
                
                with m.If(command.set_value.advance):
                    # Write to memory
                    m.d.sync += wr_port.en.eq(1)
                    m.d.sync += wr_port.data.eq(next_pixel)
                    m.d.sync += wr_port.addr.eq(Cat(writing_column, writing_row, writing_super_row))
                    # @@@ set address!

                    # Advance to next pixel
                    m.d.sync += writing_column.eq(writing_column + 1)
                    with m.If((~writing_column) == 0):
                        m.d.sync += writing_row.eq(writing_row + 1)
                        with m.If((~writing_row) == 0):
                            m.d.sync += writing_super_row.eq(writing_super_row + 1)
        return m
            

                 

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
        MAX_PWM = 14
        pwm = Signal(range(MAX_PWM + 1))

        with m.FSM(init="NEWROW"):
            with m.State("NEWROW"):
                # Blank output before updating addresses, to help avoid ghosting
                # ("OE" pin is active low)
                m.d.sync += oe.eq(1)
                m.d.sync += led_strobe.eq(0)
                m.next = "SETADDR"
            with m.State("SETADDR"):
                m.d.sync += addr.eq(row)
                m.d.sync += col.eq(0)
                m.next = "SENDDATA"
            with m.State("SENDDATA"):
                # Assume data is accepted on falling edge of led_clk
                flash = Signal()
                r1 = Signal()
                g1 = Signal()
                b1 = Signal()
                r2 = Signal()
                g2 = Signal()
                b2 = Signal()
                m.d.comb += flash.eq(pwm & 1)
                
                m.d.comb += r1.eq(row >= 7)
                m.d.comb += g1.eq(((row >= 7) & flash) | (row >= 13))
                m.d.comb += b1.eq((row <7) | (row >= 13) | flash)

                m.d.comb += r2.eq(row < 9)
                m.d.comb += g2.eq((row <= 2) | ((row <= 8)&flash))
                m.d.comb += b2.eq((row <= 2) | (row > 8) | flash)
                
                m.d.sync += rgb1.eq(Cat(r1,g1,b1))
                m.d.sync += rgb2.eq(Cat(r2,g2,b2))
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
                m.d.sync += oe.eq(0)
                m.d.sync += led_strobe.eq(1)
                m.d.sync += pwm.eq(pwm+1)
                with m.If(pwm == MAX_PWM):
                    m.d.sync += pwm.eq(0)
                    m.d.sync += row.eq(row+1)
                    m.next = "NEWROW"
                with m.Else():
                    m.next = "SENDDATA"
        

        # second_count = int(1.0/self.sys_clk_period)
        # counter=Signal(range(second_count+1))
        # m.d.sync += led_clk.eq(counter < (second_count//2))
        # m.d.sync += counter.eq(Mux(counter < second_count, counter+1, 0))

                    
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

    async def run(self, args):
        while True:
            await asyncio.sleep(1)


    @classmethod
    def tests(cls):
        from . import test
        return test.DisplayLEDPanelAppletTestCase
# glasgow run video-ledpanel --led-clk A0 --led-strobe A1 --oe A2 --addr A3,A4,A5,A6 --rgb1 B0,B1,B2 --rgb2 B3,B4,B5 --voltage 5


# TODO: Rename to include "HUB75" in name
# TODO: Display images
# TODO: Sort out parameters to include super-rows (or infer them from the pins)
