const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function fixture({supported=true,supportProbe=null}={}){
 const sockets=[],decoders=[],draws=[],frames=[],timers=[],buffers=[];
 class Socket {
  static OPEN=1;
  constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this);}
  send(data){this.sent.push(JSON.parse(data));}
  close(){this.readyState=3;}
 }
 class Decoder {
  static async isConfigSupported(){return supportProbe?await supportProbe():{supported};}
  constructor(callbacks){this.callbacks=callbacks;this.state='unconfigured';this.decodeQueueSize=0;this.chunks=[];decoders.push(this);}
  configure(options){this.options=options;this.state='configured';}
  decode(chunk){this.chunks.push(chunk);}
  reset(){this.state='unconfigured';this.decodeQueueSize=0;}
  close(){this.state='closed';}
 }
 const canvas={width:0,height:0,getContext(){return {drawImage(frame){draws.push(frame);}};}};
 class SourceBuffer {
  constructor(){this.listeners={};this.updating=false;this.buffered={length:0};this.parts=[];buffers.push(this);}
  addEventListener(event,callback){this.listeners[event]=callback;}
  appendBuffer(data){this.parts.push(data);this.updating=true;}
  complete(){this.updating=false;this.listeners.updateend();}
 }
 class MediaSource {
  static isTypeSupported(){return true;}
  addEventListener(event,callback){if(event==='sourceopen')queueMicrotask(callback);}
  addSourceBuffer(){return new SourceBuffer();}
 }
 const video={pause(){},removeAttribute(){},load(){},play(){return Promise.resolve();},requestVideoFrameCallback(){}};
 const context=vm.createContext({module:{exports:{}},ArrayBuffer,DataView,Uint8Array,Map,
   WebSocket:Socket,VideoDecoder:Decoder,MediaSource,atob,URL:{createObjectURL:()=> 'blob:video',revokeObjectURL(){}},
   EncodedVideoChunk:class {constructor(options){Object.assign(this,options);}},
   performance:{now:()=>100},setInterval:()=>1,clearInterval(){},setTimeout:(fn,ms)=>{timers.push({fn,ms});return 1;},clearTimeout(){}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../../src/kvm_mcp/app/static/h264.js'),'utf8'),context);
 const Player=context.module.exports.H264Player;
 const player=new Player({url:'wss://kvm.example/ws/h264',canvas,video,onFrame:frame=>frames.push(frame)});
 player.start();
 const config={type:'config',protocol:'KVM1',format:'annexb',codec:'avc1.42c020',generation:1};
 const packet=(sequence,key=true)=>{
  const data=new ArrayBuffer(28),v=new DataView(data);
  v.setUint32(0,0x4b564d31);v.setUint8(4,key?1:0);v.setUint32(8,sequence);v.setUint32(12,1);v.setUint32(20,sequence*16667);
  return data;
 };
 return {player,Player,sockets,decoders,draws,frames,config,packet,canvas,timers,buffers,video};
}

test('acknowledgement follows decoded frame drawing, not mere receipt',async()=>{
 const f=fixture();await f.player.configure(f.config,f.player.version);
 f.sockets[0].onmessage({data:f.packet(1)});
 assert.equal(f.sockets[0].sent.length,0);
 let closed=false;
 f.decoders[0].callbacks.output({timestamp:16667,displayWidth:1280,displayHeight:720,close(){closed=true;}});
 assert.equal(closed,true);assert.equal(f.draws.length,1);assert.equal(f.canvas.width,1280);
 assert.deepEqual(f.sockets[0].sent,[{type:'ack',sequence:1}]);
 f.player.stop();
});

test('decoder overload requests a keyframe and drops dependent deltas',async()=>{
 const f=fixture();await f.player.configure(f.config,f.player.version);
 f.sockets[0].onmessage({data:f.packet(1)});
 f.decoders[0].decodeQueueSize=2;
 f.sockets[0].onmessage({data:f.packet(2,false)});
 f.sockets[0].onmessage({data:f.packet(3,false)});
 assert.equal(f.decoders[0].chunks.length,1);
 assert.equal(f.sockets[0].sent.filter(m=>m.type==='resync').length,1);
 assert.deepEqual(f.sockets[0].sent.filter(m=>m.type==='ack').map(m=>m.sequence).sort(),[1,2,3]);
 f.sockets[0].onmessage({data:f.packet(4,true)});
 assert.equal(f.decoders[0].chunks.length,2);
 assert.equal(f.decoders[0].chunks[1].type,'key');
 f.player.stop();
});

test('unsupported WebCodecs reconnects using fMP4 over the same WSS origin',async()=>{
 const f=fixture({supported:false});
 const version=f.player.version;
 try{await f.player.configure(f.config,version);}catch(error){f.player.fail(error,version);}
 assert.equal(f.sockets[0].readyState,3);
 assert.equal(f.sockets[1].url,'wss://kvm.example/ws/h264?format=mp4');
 f.player.stop();
});

test('late output from an old generation is closed without replacing the image',async()=>{
 const f=fixture();await f.player.configure(f.config,f.player.version);
 f.sockets[0].onmessage({data:f.packet(1)});
 const old=f.decoders[0];
 await f.player.configure({...f.config,generation:2},f.player.version);
 let closed=false;
 old.callbacks.output({timestamp:16667,displayWidth:1280,displayHeight:720,close(){closed=true;}});
 assert.equal(closed,true);assert.equal(f.draws.length,0);
 assert.equal(f.sockets[0].sent.filter(m=>m.type==='ack').length,1);
 f.player.stop();
});

test('invalid media headers are rejected before being sent to a decoder',()=>{
 const f=fixture();
 assert.throws(()=>f.Player.unpack(new ArrayBuffer(24)),/Invalid/);
 const bad=f.packet(1);new DataView(bad).setUint32(0,0);
 assert.throws(()=>f.Player.unpack(bad),/Unknown/);
 f.player.stop();
});

test('MSE acknowledges appended fragments and returns pending credits on stream reset',async()=>{
 const f=fixture();f.player.format='mp4';
 const config={...f.config,format:'mp4',init:btoa('initialization')};
 await f.player.configure(config,f.player.version);
 f.sockets[0].onmessage({data:f.packet(1)});
 assert.equal(f.sockets[0].sent.length,0);
 f.buffers[0].complete(); // Init append completes; fragment now starts appending.
 assert.equal(f.sockets[0].sent.length,0);
 f.buffers[0].complete();
 assert.deepEqual(f.sockets[0].sent,[{type:'ack',sequence:1}]);
 f.sockets[0].onmessage({data:f.packet(2)});
 f.sockets[0].onmessage({data:f.packet(3)});
 await f.player.configure({...config,generation:2},f.player.version);
 assert.deepEqual(f.sockets[0].sent.map(m=>m.sequence).sort(),[1,2,3]);
 f.buffers[0].listeners.error();
 assert.equal(f.sockets[0].readyState,1,'Discarded SourceBuffer errors cannot close its replacement');
 f.player.stop();
});

test('an obsolete codec probe rejection cannot retire a newer working decoder',async()=>{
 let rejectOld;let probes=0;
 const f=fixture({supportProbe:()=>++probes===1?new Promise((_,reject)=>{rejectOld=reject;}):Promise.resolve({supported:true})});
 f.sockets[0].onmessage({data:JSON.stringify(f.config)});
 f.sockets[0].onmessage({data:JSON.stringify({...f.config,generation:2})});
 await new Promise(setImmediate);
 rejectOld(new Error('obsolete codec'));
 await new Promise(setImmediate);
 assert.equal(f.sockets.length,1);
 assert.equal(f.sockets[0].readyState,1);
 assert.equal(f.decoders.length,1);
 assert.equal(f.decoders[0].state,'configured');
 f.player.stop();
});
