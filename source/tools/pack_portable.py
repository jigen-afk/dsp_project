"""Build the embedded Windows payload used by the portable launcher."""

import hashlib
from pathlib import Path
import struct
import sys

import zstandard


def pack(source, destination):
    records = bytearray()
    for path in sorted(source.rglob("*")):
        if path.is_file():
            name = path.relative_to(source).as_posix().encode("utf-8")
            contents = path.read_bytes()
            records.extend(struct.pack("<II", len(name), len(contents)))
            records.extend(name)
            records.extend(contents)
    compressed = zstandard.ZstdCompressor(level=17, threads=2, write_checksum=True).compress(records)
    cache_id = hashlib.sha256(compressed).hexdigest()[:32].encode("ascii")
    with destination.open("wb") as file:
        file.write(b"DSPSFX02" + struct.pack("<I", len(records)) + cache_id)
        file.write(compressed)


if __name__ == "__main__":
    pack(Path(sys.argv[1]), Path(sys.argv[2]))
