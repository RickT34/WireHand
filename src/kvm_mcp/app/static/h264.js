/* KVM1 H.264 over same-origin WS/WSS. No third-party player or CDN required. */
class H264Player {
  constructor({url,canvas,video,onFrame=()=>{},onStatus=()=>{},onError=()=>{},mode='auto'}) {
    this.url=url;this.canvas=canvas;this.video=video;this.onFrame=onFrame;this.onStatus=onStatus;this.onError=onError;
    this.mode=mode;this.closed=false;this.socket=null;this.decoder=null;this.source=null;this.sourceBuffer=null;
    this.pending=[];this.decoding=new Map();this.ready=false;this.waitKey=true;this.resyncRequested=false;
    this.version=0;this.generation=0;this.configuration=0;this.frames=0;this.retries=0;this.lastOutput=0;
    this.format=mode==='mse' || typeof VideoDecoder==='undefined'?'mp4':'annexb';
  }
  start() { this.connect(); }
  send(message) { if(this.socket?.readyState===WebSocket.OPEN)this.socket.send(JSON.stringify(message)); }
  ack(sequence) { this.send({type:'ack',sequence}); }
  connect() {
    if(this.closed)return;
    const version=++this.version;
    this.ready=false;this.waitKey=true;this.pending=[];this.resyncRequested=false;
    const socket=new WebSocket(`${this.url}?format=${this.format}`);socket.binaryType='arraybuffer';this.socket=socket;
    this.onStatus({stage:'连接 H.264',format:this.format});
    socket.onmessage=event=>{
      if(this.closed || version!==this.version)return;
      try {
        if(typeof event.data==='string') {
          const message=JSON.parse(event.data);
          if(message.type==='config') {
            const configuration=++this.configuration;
            this.configure(message,version).catch(error=>{
              if(configuration===this.configuration)this.fail(error,version);
            });
          } else this.onStatus(message);
        } else {
          const packet=H264Player.unpack(event.data);
          if(packet.generation!==this.generation){this.ack(packet.sequence);return;}
          if(!this.ready) {
            if(this.pending.length>=8){this.ack(packet.sequence);this.resync();return;}
            this.pending.push(packet);
          } else this.consume(packet);
        }
      } catch(error) { this.fail(error,version); }
    };
    socket.onclose=()=>{if(!this.closed && version===this.version)this.fail(new Error('视频连接已断开'),version);};
    socket.onerror=()=>socket.close();
    this.heartbeat=setInterval(()=>{
      if(version!==this.version)return;
      this.send({type:'ping'});
      if(this.lastOutput && performance.now()-this.lastOutput>4000)this.fail(new Error('视频输出超时'),version);
    },2000);
    this.startTimer=setTimeout(()=>{
      if(!this.closed && version===this.version && !this.lastOutput)this.fail(new Error('未收到可播放的 H.264 画面'),version);
    },6000);
  }
  static unpack(data) {
    if(!(data instanceof ArrayBuffer) || data.byteLength<25)throw new Error('Invalid video message');
    const view=new DataView(data);
    if(view.getUint32(0)!==0x4b564d31)throw new Error('Unknown video protocol');
    return {key:Boolean(view.getUint8(4)&1),sequence:view.getUint32(8),generation:view.getUint32(12),
      timestamp:view.getUint32(16)*4294967296+view.getUint32(20),data:new Uint8Array(data,24)};
  }
  async configure(config,version) {
    if(config.protocol!=='KVM1' || config.format!==this.format || !/^avc1\.[0-9a-f]{6}$/i.test(config.codec))
      throw new Error('Unsupported stream configuration');
    this.ready=false;
    for(const packet of this.pending)this.ack(packet.sequence);
    this.pending=[];
    for(const item of this.decoding.values())this.ack(item.sequence);
    this.decoding.clear();
    if(this.appending?.sequence)this.ack(this.appending.sequence);
    this.disposeDecoder();
    this.generation=config.generation;this.waitKey=true;this.resyncRequested=false;
    const generation=this.generation;
    if(this.format==='annexb') {
      const options={codec:config.codec,optimizeForLatency:true}; // No description => Annex-B, SPS/PPS in IDRs.
      const support=await VideoDecoder.isConfigSupported(options);
      if(this.closed || version!==this.version || generation!==this.generation)return;
      if(!support.supported)throw new Error('WebCodecs 不支持此 H.264 配置');
      this.decoderConfig=options;
      this.decoder=new VideoDecoder({
        output:frame=>this.draw(frame,version,generation),
        error:error=>{if(generation===this.generation)this.fail(error,version);}
      });
      this.decoder.configure(options);
    } else {
      const mime=`video/mp4; codecs="${config.codec}"`;
      if(typeof MediaSource==='undefined' || !MediaSource.isTypeSupported(mime))throw new Error('浏览器不支持 H.264 / MSE');
      const source=new MediaSource();this.source=source;
      this.objectURL=URL.createObjectURL(source);this.video.srcObject=null;this.video.src=this.objectURL;
      await new Promise((resolve,reject)=>{
        this.openTimer=setTimeout(()=>reject(new Error('MSE 初始化超时')),2000);
        source.addEventListener('sourceopen',()=>{clearTimeout(this.openTimer);resolve();},{once:true});
      });
      if(this.closed || version!==this.version || generation!==this.generation)return;
      const buffer=source.addSourceBuffer(mime);this.sourceBuffer=buffer;
      buffer.addEventListener('updateend',()=>{
        if(this.sourceBuffer!==buffer)return;
        if(this.appending?.sequence)this.ack(this.appending.sequence);
        this.appending=null;this.pumpMSE();
      });
      buffer.addEventListener('error',()=>{
        if(this.sourceBuffer===buffer)this.fail(new Error('MSE 无法处理视频分片'),version);
      });
      const raw=atob(config.init);const init=Uint8Array.from(raw,char=>char.charCodeAt(0));
      this.appending={sequence:0};buffer.appendBuffer(init);
      this.video.onloadeddata=()=>{if(version===this.version && generation===this.generation)this.played();};
      this.video.onerror=()=>{if(generation===this.generation)this.fail(new Error(this.video.error?.message||'MSE 播放失败'),version);};
      this.video.play().catch(error=>{if(generation===this.generation)this.fail(error,version);});
      const rendered=()=>{
        if(this.closed || version!==this.version || generation!==this.generation)return;
        this.played();
        if(this.video.requestVideoFrameCallback)this.video.requestVideoFrameCallback(rendered);
      };
      if(this.video.requestVideoFrameCallback)this.video.requestVideoFrameCallback(rendered);
      else {
        let lastTime=-1;
        this.playTimer=setInterval(()=>{if(this.video.currentTime!==lastTime){lastTime=this.video.currentTime;rendered();}},50);
      }
    }
    if(this.closed || version!==this.version || generation!==this.generation)return;
    this.ready=true;this.onStatus({stage:'等待关键帧',codec:config.codec,format:this.format});
    const waiting=this.pending;this.pending=[];
    for(const packet of waiting)this.consume(packet);
  }
  consume(packet) {
    if(this.format==='mp4') {
      if(this.pending.length>=8){this.ack(packet.sequence);this.fail(new Error('MSE 队列积压'),this.version);return;}
      this.pending.push(packet);this.pumpMSE();return;
    }
    if(this.waitKey && !packet.key){this.ack(packet.sequence);return;}
    if(this.decoder.decodeQueueSize>=2){this.ack(packet.sequence);this.resync();return;}
    if(this.waitKey){this.waitKey=false;this.resyncRequested=false;}
    this.decoding.set(packet.timestamp,{sequence:packet.sequence,started:performance.now()});
    try {
      this.decoder.decode(new EncodedVideoChunk({type:packet.key?'key':'delta',timestamp:packet.timestamp,data:packet.data}));
    } catch(error) {
      this.ack(packet.sequence);this.decoding.delete(packet.timestamp);this.fail(error,this.version);
    }
  }
  draw(frame,version,generation) {
    try {
      if(this.closed || version!==this.version || generation!==this.generation)return;
      const item=this.decoding.get(frame.timestamp);
      if(!item)return;
      this.decoding.delete(frame.timestamp);
      if(this.canvas.width!==frame.displayWidth || this.canvas.height!==frame.displayHeight){
        this.canvas.width=frame.displayWidth;this.canvas.height=frame.displayHeight;
      }
      this.canvas.getContext('2d',{alpha:false}).drawImage(frame,0,0);
      this.lastOutput=performance.now();this.frames++;
      this.ack(item.sequence);
      this.onFrame({mode:'webcodecs',frames:this.frames,width:frame.displayWidth,height:frame.displayHeight,
        decode_ms:Math.round(performance.now()-item.started)});
    } finally { frame.close(); }
  }
  pumpMSE() {
    const buffer=this.sourceBuffer;
    if(!buffer || buffer.updating || !this.pending.length || this.closed)return;
    try {
      if(buffer.buffered.length && this.video.currentTime>1.5 && buffer.buffered.start(0)<this.video.currentTime-1.2){
        buffer.remove(0,this.video.currentTime-1);return;
      }
      this.appending=this.pending.shift();buffer.appendBuffer(this.appending.data);
    } catch(error) { this.fail(error,this.version); }
  }
  played() {
    if(this.closed || !this.video.videoWidth || this.video.readyState<2)return;
    const buffered=this.video.buffered;
    if(buffered.length){
      const end=buffered.end(buffered.length-1),lag=end-this.video.currentTime;
      if(lag>.25)this.video.currentTime=Math.max(buffered.start(buffered.length-1),end-.06);
      this.video.playbackRate=lag>.12 && lag<=.25?1.08:1;
    }
    this.lastOutput=performance.now();this.frames++;
    this.onFrame({mode:'mse',frames:this.frames,width:this.video.videoWidth,height:this.video.videoHeight});
  }
  resync() {
    for(const item of this.decoding.values())this.ack(item.sequence);
    this.decoding.clear();
    for(const packet of this.pending)this.ack(packet.sequence);
    this.pending=[];
    if(this.decoder?.state==='configured'){this.decoder.reset();this.decoder.configure(this.decoderConfig);}
    this.waitKey=true;
    if(!this.resyncRequested){this.resyncRequested=true;this.send({type:'resync'});}
  }
  disposeDecoder() {
    if(this.decoder && this.decoder.state!=='closed')this.decoder.close();
    this.decoder=null;
    clearTimeout(this.openTimer);clearInterval(this.playTimer);
    this.video.onloadeddata=this.video.onerror=null;
    if(this.objectURL){URL.revokeObjectURL(this.objectURL);this.objectURL=null;}
    if(this.source){this.video.pause();this.video.removeAttribute('src');this.video.load();}
    this.source=this.sourceBuffer=this.appending=null;
  }
  retireSocket() {
    ++this.version;clearInterval(this.heartbeat);clearTimeout(this.startTimer);
    const old=this.socket;this.socket=null;
    if(old){old.onclose=old.onerror=old.onmessage=null;old.close();}
    this.disposeDecoder();this.pending=[];this.decoding.clear();this.lastOutput=0;
  }
  fail(error,version) {
    if(this.closed || version!==this.version)return;
    const fallback=this.format==='annexb' && this.mode==='auto';
    this.retireSocket();
    if(fallback){this.format='mp4';this.retries=0;this.onStatus({stage:'改用 MSE',error:error.message});this.connect();}
    else if(this.retries++<2){this.onStatus({stage:'重新同步',error:error.message});this.retryTimer=setTimeout(()=>this.connect(),500);}
    else {this.closed=true;this.onError(error.message||String(error));}
  }
  stop() {this.closed=true;clearTimeout(this.retryTimer);this.retireSocket();}
}
if(typeof module!=='undefined')module.exports={H264Player};
