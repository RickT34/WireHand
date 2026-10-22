"""Synthetic 1080p BGR conversion + JPEG throughput, not end-to-end latency."""
import json
import resource
import subprocess
import time
from wirehand.platforms.rock5b_plus.video import pipeline

results = []
for mode in ('hardware', 'software'):
    count = 180
    source = ['videotestsrc', f'num-buffers={count}', 'pattern=smpte', '!',
              'video/x-raw,format=BGR,width=1920,height=1080,framerate=30/1']
    cmd = pipeline(source, 1920, 1080, mode)
    # No leaky queue in a throughput benchmark: count every source frame.
    start_queue = cmd.index('queue') - 1
    end_queue = cmd.index('videorate') - 1
    del cmd[start_queue:end_queue]
    cmd = cmd[:cmd.index('multipartmux')-1] + ['!', 'fakesink', 'sync=false']
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    elapsed = time.monotonic() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
    results.append({'mode':mode, 'frames':count, 'width':1920, 'height':1080,
                    'seconds':round(elapsed,3), 'frames_per_second':round(count/elapsed,1),
                    'cpu_seconds':round(cpu,3), 'cpu_one_core_percent':round(cpu/elapsed*100,1),
                    'exit_code':result.returncode, 'stderr':result.stderr.decode()[-1000:]})
print(json.dumps(results, indent=2))
