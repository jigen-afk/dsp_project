"""Build the embedded Windows payload used by the portable launcher."""

import ctypes as ct
import hashlib
from pathlib import Path
import struct
import sys


def compress(data):
    cabinet = ct.WinDLL("cabinet", use_last_error=True)
    cabinet.CreateCompressor.argtypes = [ct.c_uint, ct.c_void_p, ct.POINTER(ct.c_void_p)]
    cabinet.SetCompressorInformation.argtypes = [ct.c_void_p, ct.c_int, ct.c_void_p, ct.c_size_t]
    cabinet.Compress.argtypes = [
        ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t),
    ]
    cabinet.CloseCompressor.argtypes = [ct.c_void_p]
    handle = ct.c_void_p()
    if not cabinet.CreateCompressor(5, None, ct.byref(handle)):  # COMPRESS_ALGORITHM_LZMS
        raise ct.WinError(ct.get_last_error())
    try:
        block_size = ct.c_uint(32 * 1024**2)
        if not cabinet.SetCompressorInformation(handle, 1, ct.byref(block_size), ct.sizeof(block_size)):
            raise ct.WinError(ct.get_last_error())
        size = ct.c_size_t()
        output = ct.create_string_buffer(len(data) + 1024 * 1024)
        result = cabinet.Compress(handle, data, len(data), output, len(output), ct.byref(size))
        if not result:
            raise ct.WinError(ct.get_last_error())
        return bytes(memoryview(output).cast("B")[:size.value])
    finally:
        cabinet.CloseCompressor(handle)


def pack(source, destination):
    records = bytearray()
    for path in sorted(source.rglob("*")):
        if path.is_file():
            name = path.relative_to(source).as_posix().encode("utf-8")
            contents = path.read_bytes()
            records.extend(struct.pack("<II", len(name), len(contents)))
            records.extend(name)
            records.extend(contents)
    archive = bytes(records)
    del records
    compressed = compress(archive)
    cache_id = hashlib.sha256(compressed).hexdigest()[:32].encode("ascii")
    with destination.open("wb") as file:
        file.write(b"DSPSFX01" + struct.pack("<I", len(archive)) + cache_id)
        file.write(compressed)


if __name__ == "__main__":
    pack(Path(sys.argv[1]), Path(sys.argv[2]))
