# Ref: https://some.sort.of.reference
# Accession: G???

import asyncio
import dataclasses

from amaranth import *
from amaranth.lib import io, cdc, wiring, stream, data, memory
from amaranth.lib.wiring import In, Out, flipped

from glasgow.support import logging
from glasgow.applet import GlasgowAppletV2

__all__ = [
    "CMD_BLANK",
    "CMD_SHOW",
    "CMD_SET_SCREEN",
    "CMD_SET_WRITE",
    "CMD_ORIGIN",
    "CMD_MOVE_UP",
    "CMD_MOVE_DOWN",
    "CMD_MOVE_LEFT",
    "CMD_MOVE_RIGHT",
    "CMD_POS_STORE",
    "CMD_POS_RCL_X",
    "CMD_POS_RCL_Y",
    "DisplayLEDPanelApplet", "LEDPanelController", "LEDPanelDimensions", "pixel_shape"]

# FPGA commands
CMD_BLANK      = 0b0000 # Blank the screen (all LEDs off)
CMD_SHOW       = 0b0001 # Show the screen
CMD_SET_SCREEN = 0b0010 # Select which buffer to display
CMD_SET_WRITE  = 0b0100 # Select which buffer to write to
CMD_ORIGIN     = 0b0110 # Move to (0, 0)
CMD_MOVE_UP    = 0b1000 # Move up a pixel
CMD_MOVE_DOWN  = 0b1001 # Move down a pixel
CMD_MOVE_LEFT  = 0b1010 # Move left a pixel
CMD_MOVE_RIGHT = 0b1011 # Move right a pixel
CMD_POS_STORE  = 0b1100 # Remember the current position
CMD_POS_RCL_X  = 0b1101 # Set the current X position to the remembered one
CMD_POS_RCL_Y  = 0b1101 # Set the current Y position to the remembered one



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
    "r": pixel_r,
    "g": pixel_g,
    "b": pixel_b,
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

    def __post_init__(self):
        self.screen_shape = Shape.cast(range(MAX_SCREENS))
        self.row_shape = Shape.cast(range(self.rows))
        self.super_row_shape = Shape.cast(range(self.super_rows))
        self.column_shape = Shape.cast(range(self.columns))
        self.pixel_pos = data.StructLayout({
            "screen": self.screen_shape,
            "super_row": self.super_row_shape,
            "row": self.row_shape,
            "column": self.column_shape,
        })
        self.addr_shape = data.StructLayout({
            "column": self.column_shape,
            "row": self.row_shape,
            "screen": self.screen_shape,
        })
        self.memory_shape = data.ArrayLayout(pixel_shape, self.super_rows)
        self.rgb_shape = data.StructLayout({
            "r": unsigned(1),
            "g": unsigned(1),
            "b": unsigned(1),
        })
        self.rgb_pins = data.ArrayLayout(self.rgb_shape, self.super_rows)

    def get_memory(self):
        return memory.Memory(
            shape=self.memory_shape,
            depth=1<<self.addr_shape.size,
            init=[])


class LEDWrPortSignature(wiring.Signature):
    def __init__(self, dimensions: LEDPanelDimensions):
        wr_port_sig = memory.WritePort.Signature(
            addr_width = dimensions.addr_shape.size,
            shape = dimensions.memory_shape,
            granularity = 1,
        )
        super().__init__(wr_port_sig.members)

class LEDRdPortSignature(wiring.Signature):
    def __init__(self, dimensions: LEDPanelDimensions):
        rd_port_sig = memory.ReadPort.Signature(
            addr_width = dimensions.addr_shape.size,
            shape = dimensions.memory_shape,
        )
        super().__init__(rd_port_sig.members)

class LEDPanelSignature(wiring.Signature):
    def __init__(self, dimensions: LEDPanelDimensions):
        super().__init__({
            "led_clk": Out(1),
            "led_strobe": Out(1),
            "addr": Out(dimensions.row_shape),
            "oe": Out(1),
            "rgb": Out(dimensions.rgb_pins),
        })

class ScreenShapeSignature(wiring.Signature):
    def __init__(self, dimensions: LEDPanelDimensions):
        super().__init__({
            "screen": Out(1),
        })

@dataclasses.dataclass
class Pixel:
    r: int
    g: int
    b: int

    
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
        dimensions = LEDPanelDimensions(rows, 2, columns)
        component = assembly.add_submodule(
            LEDPanelComponent(
                ports=ports,
                dimensions=dimensions,
                sys_clk_period=assembly.sys_clk_period
            )
        )
        self._pipe = assembly.add_inout_pipe(component.o_stream, component.i_stream)

    async def set_pixel(self, pixel: Pixel):
        await self._pipe.send([
            0b1000_0000 | (pixel.r >> 4),
            0b0100_0000 | (pixel.g >> 4),
            0b0011_0000 | (pixel.b >> 4),
        ])
        
        
class LEDPanelController(wiring.Component):

    def __init__(self, dimensions: LEDPanelDimensions):
        self.dimensions = dimensions

        super().__init__({
            "i_stream": In(stream.Signature(8)),
            "wr_port": In(LEDWrPortSignature(self.dimensions)),
            "next_screen": Out(ScreenShapeSignature(self.dimensions)),
            "enable_display": Out(unsigned(1)),
        })

    def elaborate(self, platform):
        m = Module()

        # Don't write to the memory by default
        m.d.sync += self.wr_port.en.eq(0)

        writing_screen = Signal(self.dimensions.screen_shape, init=0)
        writing_row = Signal(self.dimensions.row_shape, init=0)
        writing_super_row = Signal(self.dimensions.super_row_shape, init=0)
        writing_column = Signal(self.dimensions.column_shape, init=0)
        current_pixel = Signal(pixel_shape, init={
            "r": 0,
            "g": 0,
            "b": 0
        })
        stored_row = Signal(self.dimensions.row_shape, init=0)
        stored_super_row = Signal(self.dimensions.super_row_shape, init=0)
        stored_column = Signal(self.dimensions.column_shape, init=0)

        m.d.comb += self.i_stream.ready.eq(1)
        command = Signal(command_shape)
        m.d.comb += command.eq(self.i_stream.payload)
        
        with m.If(self.i_stream.valid):
            with m.If(command.set_value.set_r |
                      command.set_value.set_g |
                      command.set_value.set_b):
                m.d.sync += Print("Command:", command)
                # This is a command to set the current pixel value, and
                # possibly write it to memory and move on
                next_pixel = Signal(pixel_shape)
                m.d.comb += next_pixel.eq(current_pixel)
                with m.If(command.set_value.set_r):
                    m.d.comb += next_pixel.r.eq(command.set_value.value)
                with m.If(command.set_value.set_g):
                    m.d.comb += next_pixel.g.eq(command.set_value.value)
                with m.If(command.set_value.set_b):
                    m.d.comb += next_pixel.b.eq(command.set_value.value)
                m.d.sync += current_pixel.eq(next_pixel)
                
                with m.If(command.set_value.advance):
                    # Write to memory
                    m.d.sync += self.wr_port.en.bit_select(writing_super_row, 1).eq(1)
                    for i in range(self.dimensions.super_rows):
                        m.d.sync += self.wr_port.data[i].eq(next_pixel)
                    m.d.sync += self.wr_port.addr.eq(Cat(writing_column, writing_row, writing_screen))
                    m.d.sync += Print("Writing ", next_pixel, "to", writing_column, writing_row, writing_super_row)

                    # Advance to next pixel
                    m.d.sync += writing_column.eq(writing_column + 1)
                    with m.If((~writing_column) == 0):
                        m.d.sync += writing_row.eq(writing_row + 1)
                        with m.If((~writing_row) == 0):
                            m.d.sync += writing_super_row.eq(writing_super_row + 1)

            with m.Else():
                with m.Switch(command.command):
                    with m.Case(CMD_SET_WRITE, CMD_SET_WRITE | 1):
                        m.d.sync += writing_screen.eq(command.command[0])
                    with m.Case(CMD_SET_SCREEN, CMD_SET_SCREEN | 1):
                        m.d.sync += self.next_screen.screen.eq(command.command[0])
                    with m.Case(CMD_BLANK, CMD_SHOW):
                        m.d.sync += self.enable_display.eq(command.command[0])
                    with m.Case(CMD_ORIGIN):
                        m.d.sync += writing_column.eq(0)
                        m.d.sync += writing_row.eq(0)
                        m.d.sync += writing_super_row.eq(0)
                    with m.Case(CMD_MOVE_RIGHT):
                        m.d.sync += writing_column.eq(writing_column + 1)
                    with m.Case(CMD_MOVE_LEFT):
                        m.d.sync += writing_column.eq(writing_column - 1)
                    with m.Case(CMD_MOVE_DOWN):
                        m.d.sync += writing_row.eq(writing_row + 1)
                        with m.If((~writing_row) == 0):
                            m.d.sync += writing_super_row.eq(writing_super_row + 1)
                    with m.Case(CMD_MOVE_UP):
                        m.d.sync += writing_row.eq(writing_row - 1)
                        with m.If(writing_row == 0):
                            m.d.sync += writing_super_row.eq(writing_super_row - 1)
                    with m.Case(CMD_POS_STORE):
                        m.d.sync += Print("Storing column ", writing_column)
                        m.d.sync += stored_column.eq(writing_column)
                        m.d.sync += stored_row.eq(writing_row)
                        m.d.sync += stored_super_row.eq(writing_super_row)
                    with m.Case(CMD_POS_RCL_X):
                        m.d.sync += Print("Resetting column to ",stored_column)
                        m.d.sync += writing_column.eq(stored_column)
                    with m.Case(CMD_POS_RCL_Y):
                        m.d.sync += writing_row.eq(stored_row)
                        m.d.sync += writing_super_row.eq(stored_super_row)
                        
        return m


class LEDPanelComponent(wiring.Component):
    """
    Top-level component for displaying on an LED panel
    """
    o_stream: Out(stream.Signature(8))
    i_stream: In(stream.Signature(8))

    def __init__(self, ports, dimensions, sys_clk_period):
        self._ports = ports
        self.dimensions = dimensions
        self.sys_clk_period = sys_clk_period

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.controller = controller = LEDPanelController(self.dimensions)
        m.submodules.driver = driver = LEDPanelDriver(self.dimensions)

        m.submodules.mem = self.mem = mem = self.dimensions.get_memory()

        # Connect up the memory
        wiring.connect(m, controller.wr_port, mem.write_port(granularity=1))
        wiring.connect(m, driver.rd_port, mem.read_port())

        # Connect the control stream
        wiring.connect(m, flipped(self.i_stream), controller.i_stream)

        # Connect the "next screen to show" signal
        wiring.connect(m, controller.next_screen, driver.next_screen)

        # Connect up the panel itself
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

        m.d.comb += led_clk_buffer.o.eq(driver.panel.led_clk)
        m.d.comb += led_strobe_buffer.o.eq(driver.panel.led_strobe)
        m.d.comb += oe_buffer.o.eq(driver.panel.oe)
        m.d.comb += rgb1_buffer.o.eq(driver.panel.rgb[0])
        m.d.comb += rgb2_buffer.o.eq(driver.panel.rgb[1])
        m.d.comb += addr_buffer.o.eq(driver.panel.addr)
        
        return m
    
                 

class LEDPanelDriver(wiring.Component):

    def __init__(self, dimensions):
        self.dimensions = dimensions


        super().__init__({
            "panel": Out(LEDPanelSignature(self.dimensions)),
            "i_stream": In(stream.Signature(8)),
            "o_stream": Out(stream.Signature(8)),
            "rd_port": In(LEDRdPortSignature(self.dimensions)),
            "vsync": Out(unsigned(1)),
            "next_screen": In(ScreenShapeSignature(self.dimensions)),
        })

    def elaborate(self, platform):
        m = Module()

        row = Signal(self.dimensions.row_shape)
        col = Signal(self.dimensions.column_shape)
        screen = Signal(self.dimensions.screen_shape, init=0)

        # For PWM purposes we count from 0 to the maximum pixel value
        # minus one. This is because we will light up a pixel if its
        # value is strictly greater than the PWM count, and we want
        # pixels with the maximum possible value to be on constantly.
        MAX_PWM = (1 << BPP) - 2
        current_pwm = Signal(range(MAX_PWM + 1))

        # Pixels of brightness level 1 are on for 1 unit of time;
        # brightness level 2 gets an additional 3, level 3 gets an
        # additional 5 on top of that, and then each successive level
        # gets (in addition to what all the lower levels got) an
        # additional amount of the next odd number.
        #
        # This means that a pixel with value n gets an on-time of
        # n^2. This gives a rough approximation to gamma correction,
        # meaning that the brightness levels appear roughly even.
        level_max = Signal(range(1 << (2 * BPP)))
        level_counter = Signal(range(1 << (2 * BPP)))

        enable_display = Signal(init=1)

        m.d.comb += self.rd_port.addr.eq(Cat(col, row, screen))
        m.d.comb += self.rd_port.en.eq(1)

        with m.FSM(init="NEWROW"):
            with m.State("NEWSCREEN"):
                m.d.sync += screen.eq(self.next_screen.screen)
                m.d.sync += self.vsync.eq(1)
                m.next = "NEWROW"
            with m.State("NEWROW"):
                m.d.sync += self.vsync.eq(0)
                # Set up PWM counters
                m.d.sync += current_pwm.eq(0)
                m.d.sync += level_counter.eq(0)
                m.d.sync += level_max.eq(0)
                
                # Blank output before updating addresses, to help avoid ghosting
                # ("OE" pin is active low)
                m.d.sync += self.panel.oe.eq(1)
                m.d.sync += self.panel.led_strobe.eq(0)
                m.next = "SETADDR"
            with m.State("SETADDR"):
                m.d.sync += self.panel.addr.eq(row)
                m.d.sync += col.eq(0)
                m.next = "SENDDATA"
            with m.State("SENDDATA"):
                # Data is accepted on rising edge of led_clk
                m.d.sync += self.panel.led_strobe.eq(0)

                for i in range(self.dimensions.super_rows):
                    m.d.sync += self.panel.rgb[i].eq(Cat(
                        self.rd_port.data[i].r > current_pwm,
                        self.rd_port.data[i].g > current_pwm,
                        self.rd_port.data[i].b > current_pwm
                    ))

                m.d.sync += self.panel.led_clk.eq(0)
                m.next = "DATACLOCK"
            with m.State("DATACLOCK"):
                m.d.sync += self.panel.led_clk.eq(1)
                m.d.sync += col.eq(col+1)
                with m.If(col == (self.dimensions.columns - 1)):
                    m.d.sync += col.eq(0)
                    m.next = "ENDROW"
                with m.Else():
                    m.next = "SENDDATA"
            with m.State("ENDROW"):
                m.d.sync += self.panel.oe.eq(~enable_display)
                m.d.sync += self.panel.led_strobe.eq(1)

                # Deal with pwm levels
                m.d.sync += level_counter.eq(level_counter+1)
                with m.If(level_counter == level_max):
                    m.d.sync += level_counter.eq(0)
                    m.d.sync += level_max.eq(level_max+2)
                
                    m.d.sync += current_pwm.eq(current_pwm+1)
                    with m.If(current_pwm == MAX_PWM):
                        m.d.sync += current_pwm.eq(0)
                        m.d.sync += row.eq(row+1)
                        with m.If(row == (self.dimensions.rows - 1)):
                            # End of screen
                            m.next = "NEWSCREEN"
                        with m.Else():
                            m.next = "NEWROW"
                    with m.Else():
                        m.next = "SENDDATA"
                with m.Else():
                    m.next = "SENDDATA"
        

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
