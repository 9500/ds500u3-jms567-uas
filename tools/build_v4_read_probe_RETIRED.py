#!/usr/bin/env python3
# RETIRED: this v4 probe hangs the controller under concurrent two-LUN reads (see docs/firmware-analysis.md §5). Kept for the record only. Do not flash.
"""Build and locally check a two-LUN cross-port READ-admission probe.

NO device access or flashing. Hardware concurrency and error recovery are NOT
validated. This image still executes writes through its generic path; it is
NOT a write blocker. Use only isolated expendable disks in physical ports 0/1.

Usage: python3 build_v4_read_probe.py EXACT_V3.bin NEW_OUTPUT.bin
The output is created exclusively: existing files are never overwritten.
"""
from pathlib import Path
from itertools import product
import argparse
import binascii
import hashlib

V3_SHA = '9b91f14bc5df0d47f42368ea60e1b9753abab807d9b751e0f4b954c0c662f3b9'
OLD_TAIL = bytes.fromhex('90 43 64 e0 ff 90 4a af e0 fe 6f 70 02 d3 22 ef 90 43 64 b4 ff 04 ee f0 d3 22')
# 0x9A16: LUN < 2; opcode READ(10) or READ(16); return C=1/0.
# Preceding gate, identity mapping and drive-capability tests remain intact.
NEW_TAIL = bytes.fromhex('90 4a af e0 c3 94 02 50 0e 90 79 0f e0 b4 28 02 d3 22 b4 88 02 d3 22 c3 22 00')

def crc(b):
    region = b[:0xBFFC]
    return binascii.crc_hqx(b''.join(region[i:i+4][::-1]
                                    for i in range(0,len(region),4)),0)

class Slice8051:
    """Subset interpreter for eligibility and the initial generic drain branch.

    Treat XDATA as ordinary memory: no controller behavior, interrupts, timing,
    DMA, SATA commands, queues or full-firmware execution are modeled.
    """
    def __init__(self, code):
        self.code = code
        self.d = bytearray(256)
        self.r = bytearray(8)
        self.x = {}
        self.c = 0
        self.pc = 0
        self.stack = []

    @property
    def a(self): return self.d[0xE0]
    @a.setter
    def a(self, v): self.d[0xE0] = v & 255
    @property
    def dp(self): return self.d[0x82] | self.d[0x83] << 8
    @dp.setter
    def dp(self, v): self.d[0x82], self.d[0x83] = v & 255, v >> 8

    def byte(self):
        n = self.code[self.pc]
        self.pc += 1
        return n

    def bit(self, n):
        addr = 0x20 + (n >> 3) if n < 0x80 else n & 0xF8
        return (self.d[addr] >> (n & 7)) & 1

    def jump(self, rel): self.pc += rel if rel < 128 else rel - 256

    def run(self, start, stop=None):
        self.pc = start
        self.stack.clear()
        for _ in range(400):
            if self.pc == stop: return
            at = self.pc
            op = self.byte()
            if op == 0x90: self.dp = self.byte() * 256 + self.byte()
            elif op == 0xE0: self.a = self.x.get(self.dp, 0)
            elif op == 0xF0: self.x[self.dp] = self.a
            elif op == 0xE4: self.a = 0
            elif op == 0x00: pass
            elif op == 0xA3: self.dp = (self.dp + 1) & 0xFFFF
            elif op == 0x54: self.a &= self.byte()
            elif op == 0x64: self.a ^= self.byte()
            elif op == 0x74: self.a = self.byte()
            elif op == 0xF5: self.d[self.byte()] = self.a
            elif op == 0xE5: self.a = self.d[self.byte()]
            elif op == 0x75:
                addr, value = self.byte(), self.byte()
                self.d[addr] = value
            elif 0x78 <= op <= 0x7F: self.r[op - 0x78] = self.byte()
            elif 0xF8 <= op <= 0xFF: self.r[op - 0xF8] = self.a
            elif 0xE8 <= op <= 0xEF: self.a = self.r[op - 0xE8]
            elif 0x88 <= op <= 0x8F: self.d[self.byte()] = self.r[op - 0x88]
            elif op in (0x24, 0x34, 0x2F):
                arg = self.r[7] if op == 0x2F else self.byte()
                total = self.a + arg + (self.c if op == 0x34 else 0)
                self.a, self.c = total, int(total > 255)
            elif op == 0x94:
                total = self.a - self.byte() - self.c
                self.a, self.c = total, int(total < 0)
            elif op == 0xA4:
                total = self.a * self.d[0xF0]
                self.a, self.d[0xF0], self.c = total, total >> 8, 0
            elif op == 0x44: self.a |= self.byte()
            elif op in (0x6E, 0x6F): self.a ^= self.r[op - 0x68]
            elif op == 0xCF: self.a, self.r[7] = self.r[7], self.a
            elif op == 0xC3: self.c = 0
            elif op == 0xD3: self.c = 1
            elif op in (0x60, 0x70, 0x40, 0x50, 0x80):
                rel = self.byte()
                take = (op == 0x80 or op == 0x60 and self.a == 0 or
                        op == 0x70 and self.a != 0 or op == 0x40 and self.c or
                        op == 0x50 and not self.c)
                if take: self.jump(rel)
            elif op in (0x20, 0x30):
                bit, rel = self.byte(), self.byte()
                if self.bit(bit) == int(op == 0x20): self.jump(rel)
            elif op == 0xB4:
                imm, rel = self.byte(), self.byte()
                self.c = int(self.a < imm)
                if self.a != imm: self.jump(rel)
            elif op == 0x12:
                target = self.byte() * 256 + self.byte() - 0x4000
                self.stack.append(self.pc)
                self.pc = target
            elif op == 0x22:
                if not self.stack: return
                self.pc = self.stack.pop()
            else: raise AssertionError(f'Unexpected opcode {op:02x} at {at:04x}')
        raise AssertionError('Instruction budget exceeded')


def setup(code, lun=0, mapped=0, opcode=0x28, gate=0x80, pm=1,
          cap=8, active=0xFF, single=1):
    cpu = Slice8051(code)
    cpu.r[7] = lun
    cpu.d[0x28] = single << 5  # bit address 0x45
    cpu.d[0x6C] = 5
    cpu.x = {0x462D: gate, 0x465D: pm, 0x4667 + lun: mapped,
             0x436F + 0x36 * mapped: cap, 0x4364: active,
             0x790F: opcode, 0x7036: 0x41}
    return cpu


def validate(v3, probe):
    cases = 0
    for gate, pm, lun, mapped, cap, active, single, opcode in product(
            (0, 0x80), (0, 1), range(10), range(10), (0, 8),
            (0, 1, 4, 0xFF), (0, 1), (0x28, 0x88, 0x2A, 0x8A, 0x35, 0x42)):
        cpu = setup(probe, lun, mapped, opcode, gate, pm, cap, active, single)
        cpu.run(0x99D6)
        capable = (lun == mapped and bool(cap)) if pm else bool(single)
        expected = bool(gate) and lun < 2 and capable and opcode in (0x28, 0x88)
        assert bool(cpu.c) == expected, (lun, mapped, opcode, active)
        # The unchanged failure path still clears this legacy selector.
        next_active = (0xFF if lun < 8 and gate and pm and not capable else active)
        assert cpu.x[0x4364] == next_active
        cases += 1
    print(f'Admission helper combinations: {cases} PASS')

    # Exhaust every possible CDB opcode and byte-sized LUN through the actual
    # outer helper, with otherwise eligible read-format hardware context.
    cases = 0
    for lun, opcode in product(range(256), range(256)):
        # Out-of-range LUNs return before dereferencing their map/capability.
        cpu = setup(probe, lun, lun if lun < 8 else 0, opcode)
        cpu.run(0x79A3)
        assert bool(cpu.c) == (lun < 2 and opcode in (0x28, 0x88)), (lun, opcode)
        cases += 1
    print(f'Outer helper opcode/LUN combinations: {cases} PASS')

    cases = 0
    for lun, opcode in product((0, 1), (0x28, 0x88)):
        # Retained format, capability, busy, error and initialization guards.
        mutations = [('x', 0x7037, 2), ('x', 0x7036, 0),
                     ('x', 0x7903, 1), ('x', 0x4774, 1),
                     ('x', 0x462D, 0), ('x', 0x4667 + lun, 4),
                     ('x', 0x436F + 0x36 * lun, 0),
                     ('x', 0x4913 + lun, 1), ('d', 0x6C, 4),
                     ('d', 0x76, 1), ('d', 0x77, 1),
                     ('d', 0x2A, 2), ('d', 0x2E, 4),
                     ('x', 0x7910, 1),
                     ('x', 0x7918 if opcode == 0x28 else 0x791E, 1)]
        if opcode == 0x88:
            mutations += [('x', 0x7911, 1), ('x', 0x7912, 1)]
        for space, address, value in mutations:
            cpu = setup(probe, lun, lun, opcode)
            getattr(cpu, space)[address] = value
            cpu.run(0x79A3)
            assert cpu.c == 0, (lun, opcode, space, address)
            cases += 1
    print(f'Retained outer rejection guards: {cases} PASS')

    # Carry result comparison for a mixed-LUN sequence. This models admission
    # only: it does not model issuance, tag allocation or completion.
    for code, expected in [(v3, [1, 0, 1, 0]), (probe, [1, 1, 1, 1])]:
        cpu = setup(code)
        cpu.x[0x4668], cpu.x[0x43A5] = 1, 8
        results = []
        for lun in (0, 1, 0, 1):
            cpu.r[7] = lun
            cpu.run(0x99D6)
            results.append(cpu.c)
        assert results == expected
    print('Mixed-LUN admission: v3 [1,0,1,0], probe [1,1,1,1] PASS')

    for count in (1, 2, 16, 31, 32):
        cpu = Slice8051(probe)
        cpu.x = {0x78E5: count, 0x4364: 0xA5}
        cpu.run(0x7D20)
        assert cpu.d[0x70:0x72] == bytes.fromhex('bdfc')
        assert cpu.x[0x4364] == 0xA5
    cpu = Slice8051(probe)
    cpu.x = {0x78E5: 0, 0x4364: 0xA5}
    cpu.run(0x7D20, 0x7D39)
    assert cpu.x[0x4364] == 0xFF
    print('Existing generic scheduler drain branches: 6 PASS')

    # Independent bit-at-a-time CRC implementation, avoiding crc_hqx here.
    check = 0
    for offset in range(0, 0xBFFC, 4):
        for value in probe[offset:offset+4][::-1]:
            check ^= value << 8
            for _ in range(8):
                check = ((check << 1) ^ (0x1021 if check & 0x8000 else 0)) & 0xFFFF
    assert check == int.from_bytes(probe[0xBFFE:0xC000], 'big') == crc(probe)
    print('Independent CRC implementation: PASS')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('v3', type=Path)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    v3 = args.v3.read_bytes()
    if len(v3) != 65536 or hashlib.sha256(v3).hexdigest() != V3_SHA:
        raise SystemExit('Refusing input: exact tested v3 SHA-256 is required.')
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an existing output.')
    assert crc(v3) == int.from_bytes(v3[0xBFFE:0xC000], 'big') == 0x2714
    assert v3[0x9A16:0x9A30] == OLD_TAIL
    assert len(OLD_TAIL) == len(NEW_TAIL) == 26
    probe = bytearray(v3)
    probe[0x9A16:0x9A30] = NEW_TAIL
    probe[0xBFFE:0xC000] = crc(probe).to_bytes(2, 'big')
    changed = [i for i, (a, b) in enumerate(zip(v3, probe)) if a != b]
    assert all(0x9A16 <= i < 0x9A30 or i in (0xBFFE, 0xBFFF) for i in changed)
    assert probe[0xC000:] == v3[0xC000:]
    validate(v3, probe)
    with args.output.open('xb') as out:
        out.write(probe)
    print(f'Changed offsets ({len(changed)}): ' + ', '.join(f'{i:04X}' for i in changed))
    print(f'CRC: {crc(probe):04X}')
    print(f'SHA256: {hashlib.sha256(probe).hexdigest()}')
    print(f'Created: {args.output}')
    print('UNTESTED HARDWARE PROBE. Not a write blocker or a production FBS implementation.')


if __name__ == '__main__':
    main()
