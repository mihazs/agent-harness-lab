"""A tiny PNG encoder for synthetic screenshots: 8-bit RGB, non-interlaced, any of the five scanline filters."""
import struct
import zlib


def _paeth(a, b, c):
    p = a + b - c
    distances = (abs(p - a), abs(p - b), abs(p - c))
    return (a, b, c)[distances.index(min(distances))]


def _chunk(kind, body):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))


def png(width, height, pixel, *, filter=0):
    """The bytes of a PNG whose pixel (x, y) is the RGB triple pixel(x, y), every scanline written with `filter`."""
    stride, previous, body = 3 * width, bytes(3 * width), b''
    for y in range(height):
        line = bytes(channel for x in range(width) for channel in pixel(x, y))
        predictors = lambda i: (0, line[i - 3] if i >= 3 else 0, previous[i], ((line[i - 3] if i >= 3 else 0) + previous[i]) // 2,
                                _paeth(line[i - 3] if i >= 3 else 0, previous[i], previous[i - 3] if i >= 3 else 0))
        body += bytes([filter]) + bytes((line[i] - predictors(i)[filter]) & 255 for i in range(stride))
        previous = line
    return (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + _chunk(b'IDAT', zlib.compress(body, 9)) + _chunk(b'IEND', b''))


def golden(width=8, height=6):
    """The synthetic golden screenshot: a blue bar on a white page."""
    return png(width, height, lambda x, y: (30, 90, 200) if y < 2 else (255, 255, 255))
