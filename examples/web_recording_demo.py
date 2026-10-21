"""Serve the recording UI against a simulated screen; no physical input is sent."""
import argparse
import asyncio
from pathlib import Path

import uvicorn
from record_replay_demo import DemoBackend
from kvm_automation.ownership import InputGate
from kvm_mcp.app.web import make_app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--output',type=Path,default=Path('artifacts/automation/web-demo'))
    args=parser.parse_args()
    backend=DemoBackend()
    class Video:
        encoded=None
        def status(self):return {'signal':True,'error':'','width':480,'height':320,'transport':'jpeg','test_source':True}
        async def next_frame(self,after):
            await asyncio.sleep(.2)
            return after+1,backend.screenshot()
        async def run(self):await asyncio.Event().wait()
    backend.create_video=lambda:Video()
    token='recording-demo-local-token-2026'
    app=make_app(token,backend,InputGate(args.output/'runtime'),start_video=False,
                 hosts=['127.0.0.1','localhost'],recordings_dir=args.output/'recordings')
    print(f'Demo: http://127.0.0.1:{args.port}/  Password: {token}',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)


if __name__=='__main__':main()
