from glasgow.applet import GlasgowAppletV2TestCase, synthesis_test, applet_v2_hardware_test
from . import DisplayLEDPanelApplet, LEDPanelController, LEDPanelDimensions
from amaranth import *
from amaranth.sim import Simulator
from amaranth.lib import wiring, stream
from amaranth.lib.wiring import In, Out, flipped

class LEDPanelControllerTester(wiring.Component):
    i_stream: In(stream.Signature(8))
    
    def __init__(self, dimensions):
        self.dimensions = dimensions
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        controller = LEDPanelController(self.dimensions)
        mem = self.dimensions.get_memory()

        wiring.connect(m, controller.wr_port, mem.write_port())
        wiring.connect(m, flipped(self.i_stream), controller.i_stream)

        return m
        

class DisplayLEDPanelAppletTestCase(GlasgowAppletV2TestCase, applet=DisplayLEDPanelApplet):
    @synthesis_test
    def test_bulld(self):
        self.assertBuilds("--led-clk A0 --led-strobe A1 --oe A2 --addr A3,A4,A5,A6 --rgb1 B0,B1,B2 --rgb2 B3,B4,B5 --voltage 5")

    def test_input(self):
        size = LEDPanelDimensions(4, 2, 4)
        dut = LEDPanelControllerTester(size)

        sim = Simulator(dut)
        pass
