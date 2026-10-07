# Firmware images

`DS500U3-JMS567-125.2.0.3-v3-uas-ncq.bin` (65,536 bytes) is the **v3** image exactly as flashed and
verified bit-perfectly on the test enclosure. It is derived from the Orico DS500U3 stock firmware
build 125.2.0.3 (`##VER:0103t`, build marker `56900103`) and is only valid as a replacement for that
exact image. Check your dump first:

```
sha256sum original.bin
8b3b090aa629e2bbf3c9303600d2dd7a6950e6355d4e950d6bea45715002a324  <- must match, or do not use this file
```

| Image | SHA256 | code CRC (`0xBFFE`) |
|---|---|---|
| stock 125.2.0.3 (not included; dump your own) | `8b3b090aa629e2bbf3c9303600d2dd7a6950e6355d4e950d6bea45715002a324` | `CABE` |
| v3 (this directory) | `9b91f14bc5df0d47f42368ea60e1b9753abab807d9b751e0f4b954c0c662f3b9` | `2714` |

`SHA256SUMS` can be checked with `sha256sum -c SHA256SUMS`.

## Byte-level difference, stock → v3 (14 + 4 bytes)

| File offset | stock | v3 | Meaning |
|---|---|---|---|
| `0x7B3D` | `E5` | `8F` | fast path: `MOV A,5Fh` → `MOV 5Fh,R7` (publish mapped SATA port) |
| `0x7DE8` `0x7DE9` | `CC EE` | `FF C0` | generic UAS path: `LCALL CCEEh` → `LCALL FFC0h` (stub) |
| `0xB5A3` | `BF` | `FF` | init: `ANL A,#0BFh` → `ANL A,#0FFh` (stop clearing the UAS bit) |
| `0xBFC0`-`0xBFC7` | `00 x8` | `90 48 DA EF F0 02 CC EE` | stub: `MOV DPTR,#48DAh; MOV A,R7; MOVX @DPTR,A; LJMP CCEEh` |
| `0xBFFE` `0xBFFF` | `CA BE` | `27 14` | code CRC |
| `0xC0E7` | `00` | `40` | NVS: UAS enable (bit 6) |
| `0xC0F3` | `00` | `80` | NVS: NCQ gate (bit 7) |

`../tools/build_patched_firmware.py original.bin v3 out.bin` produces this exact file from a stock dump.

## Status of v3

Qualified on two disks (cold start into UAS, correct identities and capacities, marker-verified LUN
routing, QD1-16 reads, 9,214 concurrent random writes read back with 0 errors, no kernel errors).
Not qualified: more than two disks, flush/FUA under load, error recovery, hot-plug, long-term use.
Per-disk NCQ works; there is no concurrency across disks (the controller hangs if that is forced,
see v4 in the docs). Use on disks you can afford to lose until you have qualified it yourself.

## Not included on purpose

- The stock image (vendor firmware; dump your own, it is also your rollback).
- v1 (UAS without the routing fix): all LUNs alias one physical disk; writes would destroy data.
- v4 (two-LUN read admission): hangs the controller under concurrent reads.
Their byte changes are documented in `../README.md` and `../docs/firmware-analysis.md`.

The firmware is JMicron/Orico property; this modified copy is provided for research and
interoperability on identical hardware, without warranty of any kind.
