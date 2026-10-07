import os, sys, mmap, time, random
dev, secs = sys.argv[1], float(sys.argv[2])
fd = os.open(dev, os.O_RDONLY | os.O_DIRECT)
size = os.lseek(fd, 0, os.SEEK_END)
buf = mmap.mmap(-1, 8192)
n = 0; lat = 0.0; t_end = time.time() + secs
while time.time() < t_end:
    off = random.randrange(0, size // 8192) * 8192
    t = time.perf_counter(); os.preadv(fd, [buf], off); lat += time.perf_counter() - t; n += 1
print(f"{dev} iops={n/secs:.1f} lat_ms={lat/n*1000:.1f}")
