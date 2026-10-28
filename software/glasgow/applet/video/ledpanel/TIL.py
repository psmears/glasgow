"""
TIL - Trivial Image Library

This is a library for simple operations on images.

What it is not:

 - It is not fast
 - It is not efficient
 - It is not fully-featured

What it is:

 - It is small
 - It is simple
 - It is written in pure Python
 - It has no external dependencies

For serious use, you want to be using something like PIL/Pillow, or
one of the Python bindings of ImageMagick, instead.

"""

import dataclasses
from typing import Self

from PIL import Image as PILImage

fn = "keyboard.ppm"


@dataclasses.dataclass
class _ImageArrayInterface:
    shape: tuple[int, ...]
    typestr: str
    data: tuple[bytearray, bool]


class Image:
    def __init__(self, width: int, height: int, bits: bytearray):
        self.size = (width, height)
        self.__array_interface__ = dataclasses.asdict(_ImageArrayInterface(
            shape=(height, width, 3), typestr="|u1", data=(bits, False)
        ))
    def __buffer__(self, flags):
        return memoryview(self.__array_interface__["data"][0])
    def __release_buffer__(self, flags):
        pass

    def convert(self, fmt) -> Self:
        self._check_format(fmt)
        return self

    def getpixel(self, where: tuple[int, int]) -> tuple[int, int, int]:
        offset = 3 * (where[0] + where[1] * self.size[0])
        values = self.__array_interface__["data"][0][offset:offset+3]
        return tuple(map(int, values))

    @classmethod
    def _check_format(cls, fmt: str) -> None:
        if fmt != "RGB":
            raise ValueError("Only 'RGB' images are supported")

    @classmethod
    def open(cls, filename: str) -> Self:
        with open(filename, "r") as f:
            words = f.read().split()
        if words[0] != ("P3"):
            raise TypeError("Can only read P3 PPM files")
        width, height, max_intensity = map(int, words[1:4])
        if max_intensity != 255:
            raise TypeError("Can only read 256-level PPM files")
        return cls(width, height, bytearray(map(int, words[4:])))

    @classmethod
    def new(cls, fmt: str, size: tuple[int, int]) -> Self:
        cls._check_format(fmt)
        return cls(*size, bytearray(size[0] * size[1] * 3))

    @classmethod
    def blend(cls, img0, img1: Image, ratio: float) -> Self:
        if img0.size != img1.size:
            raise ValueError("Images must be the same size")
        data0 = img0.__array_interface__["data"][0]
        data1 = img1.__array_interface__["data"][0]
        dest = bytearray(len(data0))
        coeff1 = int(ratio * 256)
        coeff0 = 256 - coeff1
        for i in range(len(data0)):
            dest[i] = (coeff0 * data0[i] + coeff1 * data1[i]) >> 8
        return cls(*img0.size, dest)

if __name__ == "__main__":
    img = Image.from_ppm(fn)

    pilimg = PILImage.fromarray(img)
    pilimg.show()
