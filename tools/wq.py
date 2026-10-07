# write qualification on expendable disks: N random 8K blocks per device written by QD concurrent writers
# (content = sha256(serial, offset) so every block is self-identifying), then read back and verified.
# Region: 2 GiB .. 10 GiB (keeps the marker blocks at 0, 1 GiB, 100 GiB, 500 GiB and the last MiB intact).
import os, sys, mmap, hashlib, random, subprocess, multiprocessing as mp
secs, qd = float(sys.argv[1]), int(sys.argv[2]); devs = sys.argv[3:]
LO, HI, BS = 2 << 30, 10 << 30, 8192
def serial(dev):
    for l in subprocess.run(["smartctl","-d","sat","-i",dev],capture_output=True,text=True).stdout.splitlines():
        if "Serial Number" in l: return l.split(":")[1].strip()
def block(ser, off):
    b = bytearray()
    while len(b) < BS: b += hashlib.sha256(f"{ser}:{off}:{len(b)}".encode()).digest()
    return bytes(b[:BS])
def writer(dev, ser, seed, q):
    import time
    fd = os.open(dev, os.O_RDWR | os.O_DIRECT); buf = mmap.mmap(-1, BS); rng = random.Random(seed); offs = []
    t_end = time.time() + secs
    while time.time() < t_end:
        off = rng.randrange(LO // BS, HI // BS) * BS; buf[:] = block(ser, off); os.pwritev(fd, [buf], off); offs.append(off)
    os.fsync(fd); q.put((dev, offs))
q = mp.Queue(); sers = {d: serial(d) for d in devs}
ps = [mp.Process(target=writer, args=(d, sers[d], i * 1000 + hash(d) % 1000, q)) for d in devs for i in range(qd)]
[p.start() for p in ps]; [p.join() for p in ps]
written = {d: set() for d in devs}
while not q.empty():
    d, offs = q.get(); written[d].update(offs)
for d in devs:
    fd = os.open(d, os.O_RDONLY | os.O_DIRECT); buf = mmap.mmap(-1, BS); bad = 0
    for off in sorted(written[d]):
        os.preadv(fd, [buf], off)
        if buf[:] != block(sers[d], off): bad += 1
    print(f"{d} ({sers[d]}): {len(written[d])} blocks written by {qd} writers in {secs:.0f} s ({len(written[d])/secs:.0f} write IOPS), read back: {bad} wrong")
