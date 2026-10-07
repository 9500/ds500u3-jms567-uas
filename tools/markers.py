# write (mode=write) or check (mode=check) 1 MiB marker blocks on expendable disks; identity by SATA serial via smartctl
import os, sys, mmap, hashlib, json, subprocess
mode, table = sys.argv[1], sys.argv[2]
CH = 1 << 20
OFFS_GIB = [0, 1, 100, 500]          # plus the last full MiB of the disk
def serial(dev):
    out = subprocess.run(["smartctl","-d","sat","-i",dev],capture_output=True,text=True).stdout
    for l in out.splitlines():
        if "Serial Number" in l: return l.split(":")[1].strip()
    return "?"
def marker(ser, off):
    seed = f"DS500-UAS-TEST {ser} offset={off}".encode()
    out = bytearray()
    while len(out) < CH: out += hashlib.sha256(seed + len(out).to_bytes(8,"big")).digest()
    return bytes(out[:CH])
devs = sys.argv[3:]
if mode == "write":
    tab = {}
    for dev in devs:
        ser = serial(dev); fd = os.open(dev, os.O_RDWR|os.O_DIRECT|os.O_SYNC); size = os.lseek(fd,0,os.SEEK_END)
        offs = [g<<30 for g in OFFS_GIB] + [(size//CH - 1)*CH]
        buf = mmap.mmap(-1, CH); tab[ser] = {"size": size, "offsets": {}}
        for off in offs:
            blk = marker(ser, off); buf[:] = blk; os.pwritev(fd,[buf],off)
            tab[ser]["offsets"][str(off)] = hashlib.sha256(blk).hexdigest()
        os.fsync(fd); os.close(fd); print(f"{dev} serial={ser} size={size}: wrote {len(offs)} markers")
    json.dump(tab, open(table,"w"), indent=1)
else:
    tab = json.load(open(table)); ok_all = True
    for dev in devs:
        ser = serial(dev); fd = os.open(dev, os.O_RDONLY|os.O_DIRECT); size = os.lseek(fd,0,os.SEEK_END); buf = mmap.mmap(-1, CH)
        exp = tab.get(ser)
        print(f"{dev}: INQUIRY/SMART serial={ser} size={size} ({'known' if exp else 'UNKNOWN'}; expected size {exp['size'] if exp else '?'} -> {'match' if exp and exp['size']==size else 'MISMATCH'})")
        for off, h in (exp or {}).get("offsets", {}).items():
            off = int(off)
            try:
                os.preadv(fd,[buf],off); got = hashlib.sha256(buf[:]).hexdigest()
            except OSError as e:
                got = f"READ ERROR {e}"
            # which disk's marker is this actually?
            who = "?"
            for s2, t2 in tab.items():
                if t2["offsets"].get(str(off)) == got: who = s2
            res = "OK" if got == h else f"WRONG (content belongs to {who})"
            ok_all &= (got == h); print(f"   offset {off:>14}: {res}")
        os.close(fd)
    print("ALL MARKERS CONSISTENT" if ok_all else "MARKER MISMATCH: LUN -> disk routing is wrong")
