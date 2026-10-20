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

        m.submodules.controller = controller = LEDPanelController(self.dimensions)
        m.submodules.mem = self.mem = mem = self.dimensions.get_memory()

        wiring.connect(m, controller.wr_port, mem.write_port())
        wiring.connect(m, flipped(self.i_stream), controller.i_stream)

        return m
        

class DisplayLEDPanelAppletTestCase(GlasgowAppletV2TestCase, applet=DisplayLEDPanelApplet):
    @synthesis_test
    def test_bulld(self):
        self.assertBuilds("--led-clk A0 --led-strobe A1 --oe A2 --addr A3,A4,A5,A6 --rgb1 B0,B1,B2 --rgb2 B3,B4,B5 --voltage 5")

    bytes_remaining = [0xff, 0xf1, 0xf3, 0,1,2,3]

    async def send_input(self, ctx):
        while self.bytes_remaining:
            ctx.set(self.dut.i_stream.valid, 1)
            ctx.set(self.dut.i_stream.payload, self.bytes_remaining[0])
            self.bytes_remaining = self.bytes_remaining[1:]
            print("tick")
            await ctx.tick().until(self.dut.i_stream.ready)
        ctx.set(self.dut.i_stream.valid, 0)
        await ctx.tick()
        await ctx.tick()
        await ctx.tick()
        for i in range(0, 10):
            v = self.dut.mem.data[i]
            print(ctx.get(v).r)
        

    def test_input(self):
        size = LEDPanelDimensions(4, 2, 4)
        self.dut = LEDPanelControllerTester(size)

        sim = Simulator(self.dut)
        sim.add_clock(1e-6)
        sim.add_testbench(self.send_input)
        with sim.write_vcd("lcdpanel.vcd"):
            #sim.run_until(15e-6) # 15 periods of the clocksim.run()
            sim.run()
