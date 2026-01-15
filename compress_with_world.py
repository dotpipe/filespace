#!/usr/bin/env python3
"""compress_with_world.py

Simple compressor that uses `world_package.bin` as a 16-entry home. It
chunks the input into 24-byte tokens and, when a token matches an entry in
the generated home, emits a 4-bit index; otherwise emits a 0xF marker and
places the token into an extras stream (gzipped). The compact output format:

    [4-byte chunk_count][packed bitstream bytes][4-byte extras_len][extras_blob]

A matching decompressor is provided.
"""
import argparse
import gzip
import hashlib
import struct
from typing import List

CHUNK_SIZE = 18


def load_world(path: str):
    b = open(path, 'rb').read()
    if len(b) < 32:
        raise SystemExit('world_package.bin must be at least 32 bytes')
    mapping = b[:16]
    key = b[16:32]
    return mapping, key


def gen_home_from_key(key: bytes) -> List[bytes]:
    # deterministically derive 16 chunks from key using SHA256(key||i)
    home = []
    for i in range(16):
        h = hashlib.sha256(key + bytes([i])).digest()
        home.append(h[:CHUNK_SIZE])
    return home


def chunk_file(path: str):
    d = open(path, 'rb').read()
    orig_len = len(d)
    pad = (-orig_len) % CHUNK_SIZE
    if pad:
        d += b"\x00" * pad
    return [d[i:i+CHUNK_SIZE] for i in range(0, len(d), CHUNK_SIZE)], orig_len


def compress(infile: str, out: str, world: str):
    mapping, key = load_world(world)
    home = gen_home_from_key(key)
    chunks, orig_len = chunk_file(infile)

    home_index = {h: i for i, h in enumerate(home)}

    types = []
    extras = []
    for c in chunks:
        if c in home_index:
            types.append(home_index[c])
        else:
            types.append(15)  # reserved type for extras
            extras.append(c)

    # Build LSB-first bitstream. For each run we emit:
    #  - 4 bits: type
    #  - 1 bit: F (flag). If F==0: next 3 bits = run_minus1 (0..7) => run = run_minus1+1 (1..8)
    #                  If F==1: next 3 bits = small_run_minus1 (0..7), then 5 bits M (0..31)
    #                  total run = (small_run_minus1+1) * (M+1) (1..256)

    def write_bits(buf_bytes, bitbuf, bitlen, value, bits):
        bitbuf |= (value & ((1 << bits) - 1)) << bitlen
        bitlen += bits
        while bitlen >= 8:
            buf_bytes.append(bitbuf & 0xFF)
            bitbuf >>= 8
            bitlen -= 8
        return bitbuf, bitlen

    out_bits = bytearray()
    bitbuf = 0
    bitlen = 0

    i = 0
    n = len(types)
    while i < n:
        t = types[i]
        # count full run (no upper bound)
        j = i + 1
        while j < n and types[j] == t:
            j += 1
        run = j - i

        # encode run by possibly splitting into chunks representable by spec
        rem = run
        while rem > 0:
            if rem <= 8:
                # use F=0 short form
                F = 0
                run_minus1 = rem - 1
                bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, t, 4)
                bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, F, 1)
                bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, run_minus1, 3)
                rem = 0
            else:
                # use F=1 extended form. choose small_run in 1..8 to pack as much as possible
                # choose small_run = min(8, rem) but must make (rem // small_run) >=1
                small_run = 8
                if rem < small_run:
                    small_run = rem
                # choose multiplier M so that chunks = small_run * (M+1) <= rem and M<=31
                M = min(31, (rem // small_run) - 1)
                if M < 0:
                    # fall back to short form for remaining
                    small_run = rem
                    bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, t, 4)
                    bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, 0, 1)
                    bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, small_run - 1, 3)
                    rem = 0
                else:
                    F = 1
                    bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, t, 4)
                    bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, F, 1)
                    bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, small_run - 1, 3)
                    bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, M, 5)
                    used = small_run * (M + 1)
                    rem -= used

        i = j

    # append 9-bit marker (nine 1s) into the bitstream, then flush remaining bits
    bitbuf, bitlen = write_bits(out_bits, bitbuf, bitlen, (1 << 9) - 1, 9)
    if bitlen > 0:
        out_bits.append(bitbuf & 0xFF)

    extras_blob = gzip.compress(b''.join(extras)) if extras else b''

    with open(out, 'wb') as f:
        # header: 4-byte chunk count, 8-byte original length
        f.write(struct.pack('<I', len(chunks)))
        f.write(struct.pack('<Q', orig_len))
        f.write(bytes(out_bits))
        # extras length and blob
        f.write(len(extras_blob).to_bytes(4, 'little'))
        if extras_blob:
            f.write(extras_blob)

    print(f'Wrote {out}; orig {orig_len} bytes; chunks {len(chunks)}; bitstream_bytes {len(out_bits)}; extras_chunks {len(extras)}')


def decompress(infile: str, out: str, world: str):
    mapping, key = load_world(world)
    home = gen_home_from_key(key)
    data = open(infile, 'rb').read()
    if len(data) < 12:
        raise SystemExit('input too short')
    chunks_count = struct.unpack('<I', data[:4])[0]
    orig_len = struct.unpack('<Q', data[4:12])[0]
    pos = 12

    # BitReader for LSB-first
    class BitReader:
        def __init__(self, data, pos):
            self.data = data
            self.pos = pos
            self.bitbuf = 0
            self.bitlen = 0

        def read_bits(self, n):
            while self.bitlen < n:
                if self.pos >= len(self.data):
                    raise SystemExit('truncated bitstream')
                self.bitbuf |= self.data[self.pos] << self.bitlen
                self.pos += 1
                self.bitlen += 8
            val = self.bitbuf & ((1 << n) - 1)
            self.bitbuf >>= n
            self.bitlen -= n
            return val

    br = BitReader(data, pos)
    restored = bytearray()
    extras_positions = []
    expanded = 0
    while expanded < chunks_count:
        t = br.read_bits(4)
        F = br.read_bits(1)
        if F == 0:
            run_minus1 = br.read_bits(3)
            run = run_minus1 + 1
        else:
            small_run_minus1 = br.read_bits(3)
            M = br.read_bits(5)
            run = (small_run_minus1 + 1) * (M + 1)

        if t == 15:
            for _ in range(run):
                extras_positions.append(len(restored))
                restored += b'\x00' * CHUNK_SIZE
        else:
            for _ in range(run):
                restored += home[t]
        expanded += run

    # consume a 9-bit all-ones end-of-bitstream marker if present
    try:
        ones = 0
        while ones < 9:
            b = br.read_bits(1)
            if b == 1:
                ones += 1
            else:
                ones = 0
    except SystemExit:
        # truncated bitstream or marker not found; fall back to current position
        pass

    # align to next byte for extras length
    br.bitbuf = 0
    br.bitlen = 0
    pos = br.pos
    if pos + 4 > len(data):
        raise SystemExit('missing extras length')
    extras_len = int.from_bytes(data[pos:pos+4], 'little')
    pos += 4
    extras_blob = data[pos:pos+extras_len] if extras_len else b''
    extras = gzip.decompress(extras_blob) if extras_blob else b''

    if extras_positions:
        ex_iter = iter([extras[i:i+CHUNK_SIZE] for i in range(0, len(extras), CHUNK_SIZE)])
        out_bytes = bytearray(restored)
        for p in extras_positions:
            try:
                chunk = next(ex_iter)
            except StopIteration:
                chunk = b'\x00' * CHUNK_SIZE
            out_bytes[p:p+CHUNK_SIZE] = chunk
        restored = out_bytes

    # trim to original length stored in header
    restored = restored[:orig_len]

    with open(out, 'wb') as f:
        f.write(restored)
    print('Decompressed to', out)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd')
    c = sub.add_parser('compress')
    c.add_argument('--in', dest='infile', required=True)
    c.add_argument('--out', required=True)
    c.add_argument('--world', default='world_package.bin')

    d = sub.add_parser('decompress')
    d.add_argument('--in', dest='infile', required=True)
    d.add_argument('--out', required=True)
    d.add_argument('--world', default='world_package.bin')

    args = p.parse_args()
    if args.cmd == 'compress':
        compress(args.infile, args.out, args.world)
    elif args.cmd == 'decompress':
        decompress(args.infile, args.out, args.world)
    else:
        p.print_help()


if __name__ == '__main__':
    main()
