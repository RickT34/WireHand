"""Copy one fresh shared HDMI frame over SSH without opening capture or sending input."""
import argparse
import io
import json
from pathlib import Path
import shlex
import subprocess

from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runtime',default='/run/kvm-mcp')
    args = parser.parse_args()
    remote = '''import json,os,sys,time
path=sys.argv[1]+'/frame.jpg'
with open(path,'rb') as stream:
    stat=os.fstat(stream.fileno())
    age=time.time()-stat.st_mtime
    if not 0<=age<2:
        raise RuntimeError('No fresh video frame; check HDMI signal and web capture')
    data=stream.read()
sys.stdout.buffer.write(json.dumps({'frame_file_mtime':stat.st_mtime,'age_seconds':age}).encode()+b'\\n'+data)
'''
    command = 'python3 -c ' + shlex.quote(remote) + ' ' + shlex.quote(args.runtime)
    result = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',args.host,command],
                            capture_output=True,timeout=15)
    if result.returncode:
        raise SystemExit(result.stderr.decode(errors='replace').strip())
    header,data = result.stdout.split(b'\n',1)
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(data)
    metadata = {**json.loads(header),'source_host':args.host,'output':str(args.output)}
    args.output.with_suffix('.capture.json').write_text(json.dumps(metadata,indent=2))
    print(json.dumps(metadata))


if __name__ == '__main__':
    main()
