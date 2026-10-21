const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function page(extra={}){
  const nodes=new Map(), sockets=[];
  function element(id){
    if(!nodes.has(id))nodes.set(id,{hidden:false,disabled:false,textContent:'',focusCount:0,
      classList:{toggle(){},add(){},remove(){}},addEventListener(){},
      focus(){this.focusCount++;},getContext(){return {drawImage(){}};}});
    return nodes.get(id);
  }
  class Socket{
    static OPEN=1;
    constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this);}
    send(data){this.sent.push(data);}
    close(){this.readyState=3;}
  }
  const context=vm.createContext({document:{getElementById:element,addEventListener(){}},
    window:{addEventListener(){}},location:{protocol:'http:',host:'example'},
    performance:{now:()=>0},WebSocket:Socket,setTimeout(){},setInterval(){},
    fetch:()=>new Promise(()=>{}),Blob,console,...extra});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../src/kvm_mcp/app/static/app.js'),'utf8'),context);
  return {nodes,sockets,run:code=>vm.runInContext(code,context)};
}

test('heartbeat acknowledgements do not steal focus from toolbar',()=>{
  const p=page();
  p.run('setControl(true)');
  p.sockets[0].onmessage({data:JSON.stringify({ok:true,controlling:true})});
  assert.equal(p.nodes.get('screen').focusCount,1);
});

test('recording toggle waits for server acknowledgement and exposes the download',()=>{
  const p=page();
  p.run('setControl(true)');
  const toggle=p.nodes.get('record-toggle');
  toggle.onclick();toggle.onclick();
  assert.equal(p.sockets[0].sent.length,1);
  assert.equal(JSON.parse(p.sockets[0].sent[0]).type,'record_start');
  assert.equal(toggle.disabled,true);
  p.sockets[0].onmessage({data:JSON.stringify({ok:true,controlling:true,recording:{active:true,steps:0,last:null}})});
  assert.equal(toggle.disabled,false);
  assert.match(toggle.textContent,/停止/);
  toggle.onclick();
  assert.equal(JSON.parse(p.sockets[0].sent[1]).type,'record_stop');
  p.sockets[0].onmessage({data:JSON.stringify({ok:true,controlling:true,recording:{active:false,steps:0,last:{id:'abc',status:'complete',steps:3}}})});
  assert.equal(p.nodes.get('record-download').hidden,false);
  assert.equal(p.nodes.get('record-download').href,'/api/recordings/abc/download');
  assert.match(p.nodes.get('record-status').textContent,/3 步/);
});

test('generic backend status enables input without USB or Rockchip fields',async()=>{
  const p=page({backendStatus:{platform:'another-platform',
    input:{connected:true,detail:'ready'},
    video:{signal:true,width:1280,height:720,transport:'jpeg'},
    control:{manual:false,connected:false}}});
  p.run('fetch=async()=>({ok:true,status:200,json:async()=>backendStatus})');
  await p.run('refresh()');
  assert.equal(p.nodes.get('take').disabled,false);
  assert.equal(p.nodes.get('input-status').textContent,'输入 · 已连接');
  assert.equal(p.nodes.get('video-status').textContent,'视频 · 有信号');
  assert.equal(p.nodes.get('encoder-label').textContent,'JPEG · 逐帧画面');
  assert.match(p.nodes.get('video-info').textContent,/1280 × 720/);
});

test('input waits for acknowledgment before sending next event',()=>{
  const p=page();
  p.run("send({type:'key',code:'KeyA',down:true});send({type:'key',code:'KeyA',down:false})");
  assert.equal(p.sockets[0].sent.length,1);
  p.sockets[0].onmessage({data:JSON.stringify({ok:true,controlling:true})});
  assert.deepEqual(p.sockets[0].sent.map(JSON.parse).map(x=>x.down),[true,false]);
});

test('pointer motion is coalesced but button release is preserved',()=>{
  const p=page();
  p.run("send({type:'ping'});send({type:'pointer',motion:true,x:0.1,buttons:1});send({type:'pointer',motion:true,x:0.9,buttons:1});send({type:'pointer',motion:false,x:0.9,buttons:0})");
  p.sockets[0].onmessage({data:JSON.stringify({ok:true,controlling:true})});
  p.sockets[0].onmessage({data:JSON.stringify({ok:true,controlling:true})});
  const sent=p.sockets[0].sent.map(JSON.parse);
  assert.equal(sent.length,3);
  assert.equal(sent[1].x,0.9);
  assert.equal(sent[2].buttons,0);
});

test('disconnect discards pending key presses',()=>{
  const p=page();
  p.run("send({type:'ping'});send({type:'key',code:'KeyA',down:true})");
  p.sockets[0].onclose();
  p.run('connectInput();pump()');
  assert.equal(p.sockets[2].sent.length,0);
  assert.equal(p.nodes.get('release').disabled,true);
});

test('pointer maps against the active WebRTC video rectangle',()=>{
  const p=page();
  p.nodes.get('rtc-video').getBoundingClientRect=()=>({left:100,top:50,width:800,height:450});
  p.run("display=liveVideo;controlling=true;pointer({clientX:500,clientY:275,buttons:1,preventDefault(){}})");
  const report=JSON.parse(p.sockets[0].sent[0]);
  assert.equal(report.x,.5);
  assert.equal(report.y,.5);
  assert.equal(report.buttons,1);
});

test('video fallback preserves manual control and selects canvas',()=>{
  const p=page();
  p.run("display=liveVideo;setControl(true);rtcActive=true;rtcFailed('test connection failure')");
  assert.equal(p.nodes.get('release').disabled,false);
  assert.equal(p.nodes.get('screen').hidden,false);
  assert.equal(p.nodes.get('rtc-video').hidden,true);
  assert.match(p.nodes.get('notice').textContent,/兼容画面/);
});

test('loopback SSH forwarding defaults to JPEG without attempting WebRTC',()=>{
  const p=page({location:{protocol:'http:',hostname:'127.0.0.1',host:'127.0.0.1:18080'}});
  assert.equal(p.nodes.get('transport').value,'jpeg');
  assert.equal(p.run('preferJPEG'),true);
  assert.match(p.nodes.get('notice').textContent,/SSH/);
});

test('track negotiation keeps JPEG visible until an actual video frame arrives',async()=>{
  const peers=[];
  class Peer {
    constructor(){peers.push(this);this.connectionState='new';this.iceConnectionState='new';}
    addTransceiver(){}
    createOffer(){return new Promise(()=>{});}
    close(){this.connectionState='closed';}
  }
  const p=page({RTCPeerConnection:Peer,createImageBitmap:async()=>({width:1920,height:1080,close(){}})});
  p.nodes.get('rtc-video').play=()=>Promise.resolve();
  p.nodes.get('rtc-video').hidden=true;
  p.run('startRTC()');
  peers[0].ontrack({receiver:{},streams:[{}]});
  assert.equal(p.sockets[1].readyState,1);
  assert.equal(p.nodes.get('rtc-video').hidden,true);
  await p.sockets[1].onmessage({data:new ArrayBuffer(8)});
  assert.equal(p.nodes.get('screen').hidden,false);
  assert.equal(p.nodes.get('empty').hidden,true);
  peers[0].connectionState='failed';peers[0].iceConnectionState='failed';peers[0].onconnectionstatechange();
  p.run('startRTC()');
  assert.equal(peers.length,1,'Failed automatic attempts must not repeatedly interrupt JPEG');
  p.nodes.get('transport').onchange({target:{value:'auto'}});
  assert.equal(peers.length,2,'Selecting auto explicitly permits retry');
});

test('JPEG demand accounts for transfer time and respects the bandwidth budget',async()=>{
  const timers=[];let now=0;
  const p=page({performance:{now:()=>now},setTimeout:(fn,delay)=>timers.push({fn,delay}),
    createImageBitmap:async()=>({width:1920,height:1080,close(){}})});
  const socket=p.sockets[1];socket.onopen();
  now=100;
  await socket.onmessage({data:new ArrayBuffer(250000)});
  const next=timers.at(-1);
  assert.equal(next.delay,300); // 2 Mbit / 5 Mbit/s = 400 ms, 100 ms already elapsed.
  assert.equal(socket.sent.length,1);
  now=400;next.fn();
  assert.equal(socket.sent.length,2);
  p.run('stopJPEG()');
  next.fn();
  assert.equal(socket.sent.length,2,'Retired sockets must not request another frame');
});

test('late JPEG decode cannot replace the WebRTC display',async()=>{
  let resolve,closed=false;
  const p=page({createImageBitmap:()=>new Promise(r=>{resolve=r;})});
  const pending=p.sockets[1].onmessage({data:new ArrayBuffer(8)});
  p.run('stopJPEG();screen.hidden=true;liveVideo.hidden=false;display=liveVideo');
  resolve({width:1920,height:1080,close(){closed=true;}});
  await pending;
  assert.equal(closed,true);
  assert.equal(p.nodes.get('screen').hidden,true);
  assert.equal(p.nodes.get('rtc-video').hidden,false);
});

test('decoded frames activate WebRTC even if render callback never fires',async()=>{
  let decoded=1;
  const pc={connectionState:'connected',iceConnectionState:'completed',getStats:async()=>new Map([
    ['video',{type:'inbound-rtp',kind:'video',codecId:'codec',framesDecoded:decoded,
      timestamp:decoded*1000,bytesReceived:10000,packetsReceived:12}],
    ['codec',{mimeType:'video/H264',sdpFmtpLine:'profile-level-id=42e01f'}]])};
  const p=page({testPeer:pc});
  p.nodes.get('rtc-video').requestVideoFrameCallback=()=>{};
  p.nodes.get('rtc-video').videoWidth=1920;
  p.run('peer=testPeer');
  await p.run('rtcStats()');
  decoded=2;
  await p.run('rtcStats()');
  assert.equal(p.run('rtcActive'),true);
  assert.equal(p.nodes.get('rtc-video').hidden,false);
  assert.equal(p.nodes.get('screen').hidden,true);
});

test('failure diagnostics distinguish network from decoder failures',()=>{
  const p=page();
  p.run("rtcDiagnostic={connection:'connecting',ice:'checking',bytesReceived:0,framesDecoded:0}");
  assert.match(p.run('failureReason()'),/尚未进入解码/);
  p.run("rtcDiagnostic={connection:'connected',bytesReceived:1000,framesDecoded:0}");
  assert.match(p.run('failureReason()'),/解码帧数为 0/);
});
