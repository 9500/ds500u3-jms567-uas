# Firmware analysis: Orico DS500U3, JMS567 build 125.2.0.3 (`##VER:0103t`)

All addresses below are **file offsets** in the 64 KB flash dump unless marked otherwise. The 8051
code address of flash code is **file offset + 0x4000**. XDATA (`MOVX`) and internal RAM are separate
address spaces. Claims were verified byte by byte against the dump (SHA256
`8b3b090aa629e2bbf3c9303600d2dd7a6950e6355d4e950d6bea45715002a324`); the behavioural statements were
confirmed on hardware as recorded in [test-log.md](test-log.md).

## 1. Image layout and checksum

| Region | Content |
|---|---|
| `0x0000-0xBFEF` | 8051 code (last non-zero byte at `0xBCFD`; `0xBCFE-0xBFEF` is unreferenced zero padding) |
| `0xBFF0-0xBFFF` | `55 AA AA 55`, build marker ASCII `56900103`, `00 00`, code CRC (2 bytes, big-endian) |
| `0xC000-0xFFFF` | NVS: USB VID/PID, 6-byte config block (`0xC006`), USB string descriptors (manufacturer `0xC00C`, product `0xC030`, serial `0xC054`), INQUIRY strings (`0xC0C8`), `HD` signature (`0xC0E0`), **UAS gate byte `0xC0E7`**, `0xC0F3` (feature gates), spindown seconds big-endian (`0xC0F6`), `JM` signature (`0xC0FE`) |

**Code CRC:** take bytes `[0x0000, 0xBFFC)`, reverse the byte order inside every consecutive 4-byte
group, compute CRC-16/XMODEM (poly `0x1021`, init 0, no reflection, no final XOR), store big-endian
at `0xBFFE-0xBFFF`. Reproduces the stored value on this image (`CABE`), on the v142.02.00.01
reference image (`89E6`) and on a third JMS567 image (`75D1`). Python: `binascii.crc_hqx(swapped, 0)`.
NVS is outside the CRC, so NVS-only changes need no CRC update.

Strings in code: `JMS566`, `JMS567`, `JMicron JMS569`, `Virtual CD`, `HARDWARE ERROR`,
`##VER:0103t`. No `UAS`/`UASP` string. The code never addresses NVS directly (no `MOV DPTR,#0xC0xx`
instruction exists): the NVS load routine copies the NVS into XDATA `0x3B00+` and the code works on
that copy.

The chip reports its version through vendor command `E0 F4 E7` (16-byte block, last 4 bytes BCD):
`125.2.0.3` here. The `jms567settings` tool's family check looks for `##VER:0103`; `0103t` contains
it, so the tool accepts this image without `--force`.

## 2. Initialisation and the UAS gate

Reset vector `0x0110`: `LJMP E9EDh` (file `0xA9ED`). The init routine calls, in order:

| File | Bytes | Role |
|---|---|---|
| `0xAABC` | `12 F4 D1` | default settings |
| `0xAABF` | `12 55 61` | load and parse NVS (file `0x1561`): flash `0xC000` → XDATA `0x3B00`, checks `JM` at NVS `FE-FF` and `HD` at `E0-E1` |
| `0xAAC2` | `12 58 4B` | misc init |
| `0xAAC5` | `12 F5 68` | apply settings (file `0xB568-0xB5BA`) |

The NVS loader copies NVS `0xE7` into the live settings byte XDATA `0x459A`:

```
1734  90 3B E7   MOV  DPTR,#3BE7h
1737  E0         MOVX A,@DPTR
1738  90 45 9A   MOV  DPTR,#459Ah
173B  F0         MOVX @DPTR,A
```

and NVS `0xF3` into XDATA `0x462D` (`0x173E`). The apply routine then does, **unconditionally** (both
early branches at `0xB56C`/`0xB572` converge at `0xB58E` before it):

```
B59E  90 45 9A   MOV  DPTR,#459Ah
B5A1  E0         MOVX A,@DPTR
B5A2  54 BF      ANL  A,#0BFh      ; clears bit 6 = UAS
B5A4  F0         MOVX @DPTR,A
```

The same byte is also read as flags in that routine: bit 1 (`SETB 52h`), bit 2, and bit 4 (if set,
bit 6 is cleared again at `0xB5B7`). A further routine at file `0x44C8` clears bit 6 too; it has one
caller (`0x3A94`, inside a runtime comparison of XDATA `0x475B` with `0xA1`) and is not part of init.

The configuration-descriptor builder:

```
A376  20 0A 4E   JB   0Ah,E3C7h    ; skip UAS in some other state
A379  90 45 9A   MOV  DPTR,#459Ah
A37C  E0         MOVX A,@DPTR
A37D  30 E6 47   JNB  ACC.6,E3C7h  ; skip UAS if bit 6 clear
```

When allowed, it appends the UAS alternate setting from code `0xDA81` (file `0x9A81`, 77 bytes,
SuperSpeed: 4 bulk endpoints, 1024-byte packets, SS companions with `bmAttributes 0x05` = 32 streams,
pipe-usage descriptors `04 24 01..04`) or `0xDA4C` (file `0x9A4C`, 53 bytes, USB 2.0), and adds
`0x4D` to the configuration length: `wTotalLength` `0x2C` → `0x79`. The BOT alt setting 0 itself is
not in flash (it comes from the mask ROM).

**Patch (v1 and later):** `0xB5A3` `BF` → `FF` (`ANL A,#0FFh`, a no-op) and NVS `0xC0E7` `00` → `40`.
The v142.02.00.01 reference build has mask `FB` there (clears bit 2 instead) and ships `0xC0E7=40`.

## 3. The LUN-routing defect in the UAS path

State involved:

| Location | Role (from its consumers) |
|---|---|
| XDATA `0x465D` | port-multiplier mode flag |
| XDATA `0x4667 + logical_LUN` | logical LUN → physical port table |
| IRAM `0x5E` | current logical LUN |
| XDATA `0x48DA` | **selected physical disk** used by INQUIRY, READ CAPACITY and the common command handlers (56 readers; e.g. INQUIRY's `DISKnn` digits at `0x51FA`, the per-port capacity record `0x4373 + 0x36*port` at `0x5951`) |
| IRAM `0x5F` | selected SATA port used when programming the transfer hardware (`0x703B`) |

Mapper helper, file `0x74CB` (code `0xB4CB`), three callers:

```
74CB  90 46 5D E0 60 0D         ; if XDATA[465D] == 0 goto else
74D1  74 67 2F F5 82 E4 34 46 F5 83   ; DPTR = 0x4667 + R7
74DB  E0 FF 22                  ; A = table[R7]; R7 = A; RET     (port in A and R7)
74DE  7F 00 ...                 ; else: R7 = 0 (A is 0), RET
```

| Caller | Code | Stores the result? |
|---|---|---|
| BOT, `0x6833` | `AF 5E 12 B4 CB 90 48 DA EF F0 12 A8 41` = `MOV R7,5Eh; LCALL mapper; MOV DPTR,#48DAh; MOV A,R7; MOVX @DPTR,A; LCALL A841h` | **yes** → `0x48DA` |
| generic UAS, `0x7DE2` | `AF 5E 12 B4 CB 12 CC EE E4 ...` = `MOV R7,5Eh; LCALL mapper; LCALL CCEEh; CLR A ...` | **no** (`CCEEh` at file `0x8CEE` only initialises other command state; it does not read R7/A/DPTR or touch `0x48DA`/`0x5F`) |
| UAS fast path, `0x7B39` | `FF 12 B4 CB E5 5F 44 80 90 70 3B F0` = `MOV R7,A; LCALL mapper; MOV A,5Fh; ORL A,#80h; MOV DPTR,#703Bh; MOVX @DPTR,A` | **no**: it discards the mapped port in A and uses stale `0x5F` |

Observed consequences (v1, five disks): at cold start every LUN reported model `DISK95`, 2^48 blocks
and failed every read ("LBA out of range") = uninitialised `0x48DA` (95) indexing garbage capacity
records; after a runtime switch to BOT and back, every LUN reported `DISK04` with the last disk's
serial and size = the value BOT left behind. Any write through such a LUN would have gone to that
one disk.

**Patch (v2):** redirect the `LCALL CCEEh` at `0x7DE7` to a stub in the unreferenced zero tail
(file `0xBFC0`, code `0xFFC0`): `90 48 DA EF F0 02 CC EE` = `MOV DPTR,#48DAh; MOV A,R7; MOVX @DPTR,A;
LJMP CCEEh`. Proven on hardware with serial-bound marker blocks on two disks of different size.

The v142 reference build uses a different convention (its mapper writes IRAM `0x5F`, and its INQUIRY
and capacity code read `0x5F`), so copying its mapper into this build would not fix it.

## 4. The NCQ gate and the fast path

NVS `0xC0F3` → XDATA `0x462D`. Bit 7 gates NCQ-related support (init at `0x4098` checks it together
with ATA IDENTIFY word 76 bit 8 and word 75 queue depth >= 16 before setting the per-port capability
bit 3 in record `0x436F + 0x36*port` at `0x3E50`; the eligibility helper at `0x99D6` rejects the fast
path when it is clear: `90 46 2D E0 20 E7 02 C3 22`). Bit 6 gates TRIM/DSM (`0x420C`, IDENTIFY word
169 bit 0). Stock has `0xC0F3 = 00`; v142 has `C0`.

Fast-path eligibility (`0x99D6`, inner helper) for port-multiplier disks: LUN < 8; **logical LUN must
equal the mapped physical port** (`0x99F0`: `... EF 6E 70 08`), i.e. the disk must sit in the bay
whose number equals its LUN (with empty bays in front, logical LUNs are compacted and the equality
can fail); per-port capability bit 3; and the **one-LUN admission** in XDATA `0x4364`: admit if the
requested LUN is the selected one or none is selected (then select it), otherwise reset the selection
to `FF` and reject. The outer helper (`0x79A3`) adds SIMPLE task attribute, command format, busy
and error-state checks. Rejected commands go to the generic path; the scheduler at `0x7D20` waits
while fast-command state `0x78E5` is non-zero and resets `0x4364` at `0x7D33` before generic work.
Net effect: fast (NCQ) commands are processed in groups belonging to one LUN; a command for another
LUN drains the group first.

**Patch (v3):** `0x7B3D` `E5` → `8F`: `MOV A,5Fh` becomes `MOV 5Fh,R7`, publishing the mapped port
(still in A as well, so the following `ORL A,#80h` / store to `0x703B` are unchanged); NVS
`0xC0F3` `00` → `80`. Result on hardware: random IOPS on one disk rise with queue depth
(73 → 97–123 at QD8–16), both disks together stay at ~75–83 total.

## 5. v4: removing the one-LUN admission hangs the controller

v4 replaced the 26-byte tail of the inner eligibility helper (`0x9A16-0x9A2F`) with "admit if LUN < 2
and the CDB opcode (XDATA `0x790F`) is READ(10) `0x28` or READ(16) `0x88`, else reject", leaving all
preceding checks intact and keeping writes on the generic path. On hardware, single-LUN reads were
fine, but as soon as two LUNs were read concurrently (even the kernel's own partition scans after
the flash reset) commands stopped completing: `uas_eh_abort_handler ... inflight: CMD IN`,
`uas_eh_device_reset_handler`, repeated USB device resets. The firmware's vendor commands timed out
as well while that was going on.

A plausible location for the hang is the shared fast builder's launch sequence, which ends in an
**unbounded poll**:

```
7B41  90 70 3B F0        MOVX [703Bh] = port | 0x80
7B45  90 78 06 E0 90 78 04 F0     command slot → [7804h]
7B4D  90 70 4A 74 60 F0  [704Ah] = 0x60
7B53  90 70 43 74 01 F0  [7043h] = 1          ; launch
...   (tag from [7808h] & 0x1F → command records 0x4136 + 8*tag / 0x4236 + 8*tag)
7BCE  90 70 43 E0 20 E0 F9 22     ; MOV DPTR,#7043h; MOVX A,@DPTR; JB ACC.0,7BCEh; RET
```

If the second port's launch never clears `0x7043.0`, the CPU spins there before the callers'
bookkeeping (`0x78E1`/`0x78E5`) runs. This is a static candidate, not captured state. Error recovery
is another known weak point for multi-port operation: the NCQ error-log path (`0x3BD2`, READ LOG EXT
page `0x10`) sends to the port in IRAM `0x5F`, i.e. the last submitted one, not necessarily the
failed one. Both the JMB575 (FBS-capable per its product brief) and the JMS567 silicon may be able to
do more, but this firmware's active path cannot have NCQ commands outstanding on two ports; making it
do so would need a different class of work (firmware instrumentation or a SATA protocol capture), not
byte patches to admission logic. v4 was retired and is kept only for the record.

## 6. What the measurements mean for RAID users

- Stock/BOT: ~73 random IOPS for the whole enclosure, regardless of disk count: `usb-storage` has
  one command slot for all LUNs and the firmware executes one SATA command at a time.
- v3/UAS: per-disk NCQ gives 1.4–1.7x on a single disk at queue depth 8–16. Across disks nothing
  changes, because commands to different ports are still serialised. A RAID stripe/RAIDZ request
  that touches every disk sees roughly the stock IOPS; only workloads that queue several requests
  to the *same* disk between port switches benefit. Sequential throughput over two disks: ~142 →
  ~164 MB/s.
- For random I/O on several disks, the bottleneck is the enclosure architecture (one SATA link, CBS
  port multiplier, serialising firmware), and the only real fixes are a different enclosure/HBA or
  fewer commands per request (e.g. a stripe or mirror instead of RAIDZ, so one request touches one
  disk).
