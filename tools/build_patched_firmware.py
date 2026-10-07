#!/usr/bin/env python3
"""Rebuild the v2 or v3 patched firmware for the Orico DS500U3 (JMS567, build 125.2.0.3 / ##VER:0103t)
from YOUR OWN original flash dump.

    python3 build_patched_firmware.py original.bin v3 out-v3.bin

The input must be the exact stock image this work was done on (SHA256 8b3b090a...). Any other image is
refused: the patch offsets are only known to be right for that build. The output file is created
exclusively (never overwrites). Nothing here touches hardware.

  v2: UAS enabled (NVS 0xC0E7 bit 6; init no longer clears it) + LUN routing fix on the UAS path.
      Correct multi-disk routing, no performance gain (one command at a time).
  v3: v2 + NCQ gate (NVS 0xC0F3 bit 7) + fast-path port selection fix. Per-disk NCQ works; still no
      concurrency across disks. Qualified on two disks only. See README.md for warnings.

v1 (UAS without the routing fix) aliases all LUNs to one disk and v4 hangs the controller; they are
deliberately not built by this script.
"""
import binascii, hashlib, sys
from pathlib import Path

ORIGINAL_SHA = "8b3b090aa629e2bbf3c9303600d2dd7a6950e6355d4e950d6bea45715002a324"
EXPECTED = {
    "v2": ("9f6109b30d5b0ef064dae2922ead500d35808e302d69740e4b316075891e11ee", 0x6194),
    "v3": ("9b91f14bc5df0d47f42368ea60e1b9753abab807d9b751e0f4b954c0c662f3b9", 0x2714),
}

def code_crc(img):
    r = img[:0xBFFC]
    return binascii.crc_hqx(b"".join(r[i:i + 4][::-1] for i in range(0, len(r), 4)), 0)

def main():
    if len(sys.argv) != 4 or sys.argv[2] not in EXPECTED:
        sys.exit("usage: build_patched_firmware.py ORIGINAL.bin v2|v3 OUTPUT.bin")
    src, version, dst = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    d = bytearray(src.read_bytes())
    if len(d) != 65536 or hashlib.sha256(d).hexdigest() != ORIGINAL_SHA:
        sys.exit("refusing: input is not the exact stock image this patch was made for (SHA256 8b3b090a...)")
    if code_crc(d) != int.from_bytes(d[0xBFFE:0xC000], "big") or d[0xBFFE:0xC000] != b"\xca\xbe":
        sys.exit("refusing: stock image CRC mismatch")
    # sanity on every patch site
    assert d[0xB5A3] == 0xBF and d[0xC0E7] == 0x00
    assert d[0x7DE7:0x7DEA] == bytes([0x12, 0xCC, 0xEE]) and all(b == 0 for b in d[0xBFC0:0xBFC8])
    assert d[0x7B3D:0x7B3F] == bytes([0xE5, 0x5F]) and d[0xC0F3] == 0x00

    # v1 part: keep the UAS bit (ANL A,#0BFh -> ANL A,#0FFh) and set it in NVS
    d[0xB5A3] = 0xFF
    d[0xC0E7] = 0x40
    # v2 part: on the generic UAS path, store the mapped physical port (R7) into XDATA 0x48DA
    #   LCALL CCEEh at 0x7DE7 -> LCALL FFC0h; stub at code 0xFFC0 (file 0xBFC0):
    #   MOV DPTR,#48DAh; MOV A,R7; MOVX @DPTR,A; LJMP CCEEh
    d[0x7DE7:0x7DEA] = bytes([0x12, 0xFF, 0xC0])
    d[0xBFC0:0xBFC8] = bytes([0x90, 0x48, 0xDA, 0xEF, 0xF0, 0x02, 0xCC, 0xEE])
    if version == "v3":
        # fast path: MOV A,5Fh -> MOV 5Fh,R7 (publish the mapped port, A keeps it); NCQ gate on
        d[0x7B3D] = 0x8F
        d[0xC0F3] = 0x80
    d[0xBFFE:0xC000] = code_crc(d).to_bytes(2, "big")

    sha, crc = EXPECTED[version]
    got = hashlib.sha256(d).hexdigest()
    if got != sha or code_crc(d) != crc:
        sys.exit(f"internal error: result does not match the tested {version} image ({got})")
    with dst.open("xb") as f:
        f.write(d)
    print(f"{version} written to {dst}: SHA256 {got}, code CRC {crc:04X}")
    print("Flash with jms567settings: python3 jms_settings.py -d /dev/sdX -y restore", dst)
    print("Then cold boot the enclosure (mains AND USB unplugged). Qualify on expendable disks first.")

if __name__ == "__main__":
    main()
