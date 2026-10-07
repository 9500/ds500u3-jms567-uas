# Hardware test log (October 2026)

Two Orico DS500U3 units ("enclosure 1" and "enclosure 2"), identical firmware. Host: Linux 7.0
(xhci), tools from this repository plus [jms567settings](https://github.com/xx25/jms567settings)
with the `py3_sg.py` shim. Random-read numbers are 8 KB `O_DIRECT` reads at random offsets over the
whole disk; "QDn" means n concurrent single-outstanding readers on that device. All disks used for
flashing experiments were expendable. Times are local time on the test day.

## Day 1 (2026-10-06), enclosure 1, five Hitachi 1 TB disks (production unit at the time)

**BOT baseline, idle disks**

```
random 8K read, 1 disk, QD1, 8 s:            70.2 IOPS, 14.3 ms
random 8K read, 4 disks concurrently, QD1:   18.2 + 18.6 + 18.9 + 18.6 = 74 IOPS total, ~54 ms each
sequential read, 1 disk:                     104 MB/s
sequential read, 2 disks concurrently:       71 + 71 = 142 MB/s
```

**Read-only tool run**

```
Firmware version (chip-reported): 125.2.0.3
Flash SHA256: 8b3b090aa629e2bbf3c9303600d2dd7a6950e6355d4e950d6bea45715002a324
USB VID:PID: 152d:0567   iProduct 'External USB 3.0'   iSerialNumber '20170331000C3'
Internal ver: '0103t'    Build marker: '56900103', checksum cabe
UAS gate (NVS[0xC0E7]): 0x00 (BOT only)   Spindown: 600 s   config block: 0a0b03010a00
```

Two dumps identical, `verify` PASS. Pre-flight of the shim's write path: wrote NVS sector `0xC000`
with its identical content, full flash unchanged.

**NVS-only change (`set-uas on`)**: the tool erases and rewrites the whole flash, resets the chip;
its "reopen" fails because LUN 0 re-enumerates under a new name (`sdi` → `sdj`); `verify` on the new
name PASS, UAS gate `0x40`. Descriptor still BOT-only before and after a cold boot at the button
(off 56 s) **and** after a second cold boot with mains and USB unplugged (73 s): `wTotalLength
0x002c`, one alt setting, protocol 80. The bit persists but has no effect. (Explanation found later:
the init code clears it, [firmware-analysis.md §2](firmware-analysis.md).) Enclosure 1 was left in
this state (original code, inert NVS bit) and works like stock.

## Day 1, enclosure 2, one expendable Hitachi 1 TB disk in bay 3

Enclosure 2: same `152d:0567`, same `bcdDevice 52.03`, **same USB serial `20170331000C3`**,
byte-identical firmware (two dumps, 0 bytes differ from enclosure 1). BOT baseline on this (slow,
damaged) disk alone: 73 IOPS random, 64 MB/s sequential.

**v1** (`0xB5A3` BF→FF, CRC 62C9, `0xC0E7`=40; SHA256 `64cf7081…`): after the tool's warm reset the
descriptor already showed `wTotalLength 0x0079`, alt 0 protocol 80 and alt 1 protocol 98; the kernel
bound `uas` (`scsi hostN: uas`, queue depth 30, "Disabling FUA" from the BROKEN_FUA quirk), no
errors. Single disk over UAS: random QD1 72 IOPS; 8 concurrent readers 8 x 9.1 = 73 total (no NCQ);
sequential 81 MB/s (BOT: 64); 1 GiB random data written with `O_DIRECT|O_SYNC` per 1 MiB and read
back: 28 MB/s / 89 MB/s, SHA256 match; stress with 8 readers + a sequential writer: clean.

## Day 1, enclosure 2, four pool disks + the test disk (five disks), v1

Cold boot (both cables unplugged). `uas` bound, five LUNs, queue depth 30, **all bogus**: model
`USB3.0 DISK95`, capacity 281474976710656 blocks (128 PiB), no serial, every READ `Illegal Request /
Logical block address out of range`, "unable to read partition table". SCSI rescan and
`USBDEVFS_RESET` changed nothing.

Runtime switch to BOT (`echo 152d:0567:u > /sys/module/usb_storage/parameters/quirks`, unbind
`uas`, bind `usb-storage`): after ~20 s all five disks correct (`DISK00..DISK04`, right sizes,
serials, partition labels), no errors; the ZFS pool on the four disks was visible to `zpool import`
(scan only). Switch back to `uas` with the disks spun up: LUNs no longer bogus but **all five
reported the same disk** (`DISK04`, the last disk's serial and size, even the LUN whose disk is
smaller). Switched back to BOT immediately; nothing was written. The pool disks were then removed
for the remaining experiments.

## Day 1, enclosure 2, two expendable disks (disk A Hitachi 1 TB in bay 3, disk B Samsung HD103SJ 1 TB), v2 → v3 → v4

With two disks present the port multiplier compacts LUNs: disk A = LUN 0 = `DISK00`, disk B = LUN 1
= `DISK01` (the LUN == port condition for the fast path holds). Under BOT, five serial-bound 1 MiB
marker blocks were written per disk (offsets 0, 1 GiB, 100 GiB, 500 GiB, last MiB; `tools/markers.py`).

**v2** (+ stub at `0xBFC0`, call redirect at `0x7DE7`; CRC 6194; SHA256 `9f6109b3…`), flashed under
BOT, `verify` PASS, markers OK under BOT, quirk cleared, cold boot with both cables out:

```
uas bound; LUN 0 = disk A, LUN 1 = disk B, correct models/serials/capacities at first enumeration
markers: 10/10 OK on the right LUN; 200 alternating LUN0/LUN1 reads: 0 wrong
4 readers per LUN over all marker offsets, 10 s: 1275 + 1276 verified 1 MiB reads, 0 wrong (128 + 128 MB/s)
random 8K QD1, one disk: 72 IOPS; both disks: 36 + 36 = 73 total; 8 readers on one disk: 73 total
sequential: one disk 137 MB/s; both 82 + 82 = 164 MB/s
kernel log clean
```

v2 baseline matrix (6 s each, both disks installed): disk A QD1/8/16 = 70.0/73.3/72.5 IOPS;
disk B 72.8/73.8/75.3; both QD1 = 72.3 total; both QD8 = 74.0 total. Flat everywhere.

**v3** (+ `0x7B3D` E5→8F, `0xC0F3`=80; CRC 2714; SHA256 `9b91f14b…`), flashed, tool's own verify
OK, cold boot with both cables out; `uas`, LUN 0 `DISK00` = disk A, LUN 1 `DISK01` = disk B, markers
OK before and after:

| v3, both installed | IOPS | mean latency |
|---|---|---|
| disk A QD1 / QD8 / QD16 | 68.8 / **97.0** / **101.7** | 14 / 83 / 160 ms |
| disk B QD1 / QD8 / QD16 | 73.8 / **109.5** / **123.3** | 14 / 74 / 131 ms |
| both, QD1 each | 37.8 + 37.8 = 75.7 | 27 ms |
| both, QD8 each | 41.3 + 41.2 = 82.5 | 197 ms |

Write qualification under v3 (`tools/wq.py`, region 2-10 GiB, 8 concurrent random 8 KB writers per
disk for 20 s, self-identifying block contents, every block read back): disk A 4622 blocks (231 write
IOPS), disk B 4592 (230), **0 wrong**, markers intact, kernel log clean.

**v4** (eligibility tail `0x9A16-0x9A2F` replaced: admit READ(10)/READ(16) for LUN 0 and 1 regardless
of the one-LUN selection; CRC 95D6; SHA256 `2984bce8…`), flashed 23:02 (verification needed retries:
the vendor flash-read commands timed out while the kernel scanned both disks after the reset), no
cold boot:

```
markers one LUN at a time: OK;  random QD1 one disk alone: 70.5 / 71.8 IOPS
both disks QD1 concurrently: no completions; kernel: uas_eh_abort_handler ... inflight: CMD IN,
  uas_eh_device_reset_handler start/success, usb 4-2: reset SuperSpeed USB device (repeated)
```

Reproducible: single-LUN fine, any two-LUN concurrency hangs. v4 retired. Rolled back to v3
(bridge switched to BOT first so the two-LUN hang could not interfere with the vendor commands):
`verify` PASS, markers OK. Enclosure 2 kept on v3 as a test unit.

## Summary of the numbers

| | stock (BOT) | v3 (UAS + per-disk NCQ) |
|---|---|---|
| one disk, QD1 | 70-73 IOPS | 69-74 IOPS |
| one disk, QD8 / QD16 | 73 / 73 | 97-110 / 102-123 |
| two disks, QD1 each | 72-74 total | 76 total |
| two disks, QD8 each | 74 total | 83 total |
| sequential, one disk | disk-bound (104-137 MB/s) | same |
| sequential, two disks | 142 MB/s | 164 MB/s |
