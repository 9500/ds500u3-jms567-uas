# Orico DS500U3 (JMicron JMS567 + JMB575): enabling UAS, and why it does not help much

An investigation into why the Orico DS500U3 5-bay USB 3.0 enclosure only speaks Bulk-Only
Transport (BOT) although its JMS567 bridge supports UASP, what it took to switch UAS on by patching
the firmware, and what the hardware then actually delivers. Done in October 2026 on two DS500U3
units with identical firmware (chip-reported version 125.2.0.3, internal marker `##VER:0103t`,
USB ID `152d:0567`, `bcdDevice 52.03`).

**Short version**

| Image | Change vs. stock | Result on hardware |
|---|---|---|
| stock 125.2.0.3 | — | BOT only. The whole enclosure executes **one SCSI command at a time**, shared by all five bays (~73 random IOPS total, no matter how many disks). |
| v1 | NVS UAS bit + remove the code that clears it | UAS alt setting appears, `uas` driver binds, but **every LUN aliases one physical disk**. Dangerous. Retired. |
| v2 | + store the mapped physical port on the UAS command path | Correct LUN routing from cold start, verified with per-disk markers. No performance change: still one command at a time. |
| **v3** | + enable the NCQ gate (NVS `0xC0F3` bit 7) + fix the fast path's port selection | **Per-disk NCQ works**: one disk 73 → 97–123 random IOPS at QD8–16. Still **no concurrency across disks** (two disks together ≈ 75–83 IOPS). Writes verified. This is the image provided here. |
| v4 | + admit queued READs from two LUNs at once | **Controller hangs** on concurrent reads to two ports (UAS aborts, device resets). Retired. The one-LUN admission in the firmware is load-bearing: the JMS567/JMB575 path does command-based switching only. |

So: UAS is enableable, the firmware's UAS path has a genuine bug (missing store of the mapped port),
and NCQ per disk is reachable, but the enclosure cannot have commands in flight on two SATA ports at
once. For a workload that touches several disks per request (any RAID), the gain is small;
sequential throughput over several disks improves by ~15 %.

Everything here was produced with the enclosure's own dumped firmware; the vendor images were
analysed offline (8051 disassembly) and the changes are a handful of bytes. See
[docs/firmware-analysis.md](docs/firmware-analysis.md) for the mechanism and addresses and
[docs/test-log.md](docs/test-log.md) for the chronological hardware record with measurements.

## Hardware

- Orico DS500U3: 5 x 3.5" SATA bays, one USB 3.0 (5 Gbps) uplink. Inside: a **JMicron JMS567**
  USB-to-SATA bridge (single SATA port; 8051 core, firmware in a 64 KB SPI flash, FM25F01) and a
  **JMicron JMB575** 1:5 SATA port multiplier (with its own flash). USB → JMS567 → one SATA link →
  JMB575 → 5 disks.
- Both units tested report the **same USB serial string `20170331000C3`**; Orico evidently flashes
  the same NVS into every unit. Their firmware images were byte-identical.
- Linux binds `usb-storage` (the firmware advertises no UAS alternate setting); the kernel's quirks
  for `152d:0567` are `US_FL_BROKEN_FUA | US_FL_NO_REPORT_OPCODES`, *not* `US_FL_IGNORE_UAS`, so the
  kernel is not what blocks UAS.
- Under `usb-storage` the SCSI host has `can_queue = 1` and every LUN `queue_depth = 1`: one
  outstanding command for the whole box. Measured: 70 IOPS random 8 KB reads on one disk, **74 IOPS
  total** with four disks read concurrently (18.5 each, 4x the latency). Sequential: ~104–137 MB/s
  one disk (disk-bound), ~142 MB/s total for two.

## Why the stock firmware is BOT-only

The NVS (settings area at flash `0xC000`) has a UAS enable bit at `0xC0E7` bit 6 (the same byte the
[xx25/jms567settings](https://github.com/xx25/jms567settings) tool toggles successfully on a
Yottamaster PS500C3). On this Orico build the bit is loaded into the live settings byte (XDATA
`0x459A`) and then **unconditionally cleared by the initialisation code** (`ANL A,#0BFh` at file
offset `0xB5A2`). The descriptor builder later tests that bit and omits the UAS alternate setting.
Flipping the NVS bit therefore does nothing; it needs the one code byte (`BF` → `FF`) too. The
complete UAS descriptor templates (interface alt 1, protocol 0x62, four bulk endpoints with pipe
usage descriptors, SuperSpeed companions with 32 streams) are present in the code at file
`0x9A4C`/`0x9A81`.

Why Orico did it: the UAS command path in this build never stores the mapped physical port into
the variable the common handlers use (XDATA `0x48DA`); BOT does. With UAS enabled as-is, every LUN
reports and reads the same disk (v1). Fixing that (v2) is an 8-byte stub plus a redirected call.

## The images

All offsets are file offsets in the 64 KB dump. Code CRC = CRC-16/XMODEM over `[0x0000, 0xBFFC)` with
each 4-byte group byte-reversed, stored big-endian at `0xBFFE`. NVS (`0xC000+`) is outside the CRC.

| | v1 | v2 | v3 (provided) | v4 (retired) |
|---|---|---|---|---|
| `0xB5A3` BF→FF (don't clear UAS bit) | x | x | x | x |
| `0xC0E7` 00→40 (NVS UAS enable) | x | x | x | x |
| `0x7DE8-9` CC EE→FF C0 (call stub) + stub `90 48 DA EF F0 02 CC EE` at `0xBFC0` | | x | x | x |
| `0x7B3D` E5→8F (`MOV A,5Fh` → `MOV 5Fh,R7`: publish mapped port on fast path) | | | x | x |
| `0xC0F3` 00→80 (NCQ gate) | | | x | x |
| `0x9A16-0x9A2F` new eligibility tail (two-LUN READ admission) | | | | x |
| code CRC | 62C9 | 6194 | **2714** | 95D6 |
| SHA256 | `64cf7081…` | `9f6109b3…` | **`9b91f14b…`** | `2984bce8…` |

`firmware/DS500U3-JMS567-125.2.0.3-v3-uas-ncq.bin` is the v3 image exactly as flashed and
verified bit-perfectly on the test unit (SHA256
`9b91f14bc5df0d47f42368ea60e1b9753abab807d9b751e0f4b954c0c662f3b9`). It is **only meaningful for
the identical stock image** (SHA256 `8b3b090aa629e2bbf3c9303600d2dd7a6950e6355d4e950d6bea45715002a324`,
CRC `CABE`). `tools/build_patched_firmware.py` rebuilds v2 or v3 from your own dump and refuses any
other input.

## What v3 is and is not

Qualified on **two disks** (one Hitachi 1 TB, one Samsung 1 TB, different capacities), true cold
start straight into UAS: correct identities and capacities, 10 serial-bound marker blocks per disk
read back on the right LUN, 200 alternating reads, 2,551 verified concurrent 1 MiB reads, 9,214
concurrent random 8 KB writes read back with 0 errors, kernel log clean. Measurements (6 s runs,
random 8 KB `O_DIRECT` reads over the whole disk):

| both disks installed | stock/v2 | v3 |
|---|---|---|
| disk A QD1 / QD8 / QD16 | 70 / 73 / 73 | 69 / **97** / **102** |
| disk B QD1 / QD8 / QD16 | 73 / 74 / 75 | 74 / **110** / **123** |
| both, QD1 each | 72 total | 76 total |
| both, QD8 each | 74 total | 83 total |

Not qualified: more than two disks, flush/FUA behaviour under load, error recovery (the firmware's
READ LOG EXT recovery uses the last-submitted port), hot-plug, long-term stability. The one-LUN
admission that remains in v3 is what keeps the controller alive (see v4); do not remove it.

**Warnings**

- Flashing erases and rewrites the whole 64 KB flash and resets the chip. A failed write can leave
  the bridge non-enumerating; recovery is shorting the SPI flash's CS pin (pin 1 of the FM25F01
  next to the JMS567) to VCC (pin 8) or GND during power-on to force mask-ROM mode, then reflashing
  (see [jms567ctl issue #4](https://github.com/projectgus/jms567ctl/issues/4)). Have the original
  dump, and know where that chip is, before you start.
- Never run v1 or v4 with more than one disk. v1 writes would go to the wrong disk; v4 hangs.
- To force a Linux host back to BOT for a UAS-advertising unit:
  `echo 152d:0567:u > /sys/module/usb_storage/parameters/quirks` before the enclosure enumerates
  (or `usb-storage.quirks=152d:0567:u` on the kernel command line).
- The firmware is JMicron/Orico property. The v3 image is provided for research and interoperability
  on the identical hardware; no warranty of any kind. Use at your own risk, on disks you can lose.

## How to dump, patch and flash

1. Get [xx25/jms567settings](https://github.com/xx25/jms567settings) (BSD-3). It needs the `py3_sg`
   C extension; `tools/py3_sg.py` is a drop-in pure-Python replacement (SG_IO via ctypes) if you
   cannot build it. Place it next to `jms_settings.py`.
2. With the pool/filesystems on the disks **unmounted/exported**, dump and verify:
   ```
   python3 jms_settings.py -d /dev/sdX info
   python3 jms_settings.py -d /dev/sdX dump original.bin
   python3 jms_settings.py -d /dev/sdX dump original-2.bin && cmp original.bin original-2.bin
   python3 jms_settings.py -d /dev/sdX verify original.bin
   ```
   `/dev/sdX` is any disk behind the bridge. Keep `original.bin` in several places.
3. Build the patched image from your dump (refuses anything but the exact stock image):
   ```
   python3 tools/build_patched_firmware.py original.bin v3 out-v3.bin
   sha256sum out-v3.bin   # 9b91f14b…
   ```
4. Flash. If more than one disk is present and the unit already advertises UAS, switch the host to
   BOT first (quirk above, unbind `uas`, bind `usb-storage`); the vendor commands are reliable there.
   Note the tool's `-y` goes **before** the subcommand:
   ```
   python3 jms_settings.py -d /dev/sdX -y restore out-v3.bin
   ```
   The tool's final "reopen" usually fails because the disks come back under new `sdX` names; the
   write is complete. Verify on the new name: `python3 jms_settings.py -d /dev/sdY verify out-v3.bin`.
5. Cold boot the enclosure (mains **and** USB cable unplugged ~15 s): the mask ROM reads NVS only at
   cold boot. Check: `lsusb -v -d 152d:0567 | grep -E 'wTotalLength|bInterfaceProtocol'` should show
   `0x0079` and both `80 Bulk-Only` and `98 USB Attached SCSI`; `lsusb -t` shows `Driver=uas`.
6. Qualify on expendable disks before trusting it with data: `tools/markers.py` (serial-bound marker
   blocks per disk; proves LUN→disk routing), `tools/bench.py` (random-read IOPS at chosen queue
   depths), `tools/wq.py` (concurrent random writes with full read-back verification).

## Repository layout

```
README.md                      this file
docs/firmware-analysis.md      mechanism: NVS, init path, descriptor builder, routing defect, NCQ gate, CRC, v4 failure
docs/test-log.md               chronological hardware record with raw numbers
firmware/                      v3 image, SHA256SUMS, byte-level diff tables
tools/build_patched_firmware.py  rebuild v2/v3 from an original dump (hash-checked)
tools/py3_sg.py                pure-Python SG_IO shim for jms567settings
tools/markers.py, bench.py, wq.py, rr.py   qualification and benchmark scripts
tools/build_v4_read_probe_RETIRED.py       the v4 builder, kept for the record; do not use
```

## Credits

- [xx25/jms567settings](https://github.com/xx25/jms567settings): the NVS/flash tool (vendor SCSI
  opcodes `0xDF`/`0xE0`/`0xFF`, flash layout, UAS gate bit).
- [projectgus/jms567ctl](https://github.com/projectgus/jms567ctl) and its
  [issue #4](https://github.com/projectgus/jms567ctl/issues/4): unbricking procedure and community
  firmware reports (the v142.02.00.01 image that enables UAS on an IcyBox IB-3640SU3 was used as a
  comparison reference; it keeps the same one-LUN admission policy).
- The firmware disassembly and the patch designs came from an offline analysis of the dumped image;
  every claim used here was re-verified byte by byte against the dump before flashing.

## License

Code and documentation in this repository: MIT (see `LICENSE`). The firmware image in `firmware/`
is derived from JMicron/Orico vendor firmware and is not covered by that license; it is provided
as-is for research and interoperability.
