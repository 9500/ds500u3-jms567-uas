# random 8K O_DIRECT read benchmark: bench.py SECONDS dev:QD [dev:QD ...]; QD = number of reader processes per device
import os, sys, mmap, time, random, multiprocessing as mp
secs = float(sys.argv[1]); specs = [(s.split(":")[0], int(s.split(":")[1])) for s in sys.argv[2:]]
def reader(dev, q):
    fd = os.open(dev, os.O_RDONLY | os.O_DIRECT); size = os.lseek(fd, 0, os.SEEK_END); buf = mmap.mmap(-1, 8192)
    rng = random.Random(os.getpid()); n = 0; lat = 0.0; t_end = time.time() + secs
    while time.time() < t_end:
        off = rng.randrange(0, size // 8192) * 8192
        t = time.perf_counter(); os.preadv(fd, [buf], off); lat += time.perf_counter() - t; n += 1
    q.put((dev, n, lat))
q = mp.Queue(); ps = [mp.Process(target=reader, args=(d, q)) for d, qd in specs for _ in range(qd)]
[p.start() for p in ps]; [p.join() for p in ps]
agg = {}
while not q.empty():
    d, n, lat = q.get(); a = agg.setdefault(d, [0, 0.0]); a[0] += n; a[1] += lat
tot = 0
for d, qd in specs:
    n, lat = agg[d]; tot += n / secs
    print(f"  {d} QD{qd}: {n/secs:6.1f} IOPS, mean latency {lat/max(n,1)*1000:6.1f} ms")
print(f"  total: {tot:.1f} IOPS")
