const $ = id => document.getElementById(id);
const screen = $('screen'), ctx = screen.getContext('2d', {alpha:false});
const liveVideo=$('rtc-video');
let display=screen, peer=null, rtcId=null, rtcStarting=false, rtcGeneration=0, rtcRetryAt=0, preferJPEG=false, rtcActive=false, rtcSuppressed=false;
let rtcFrames=0, previousRtcStats=null, rtcRtt=null;
let rtcDiagnostic={stage:'尚未协商'}, lastRTCFailure=null;
let input, video, controlling=false, queue=[], busy=false, lastState=null, lastFrame=0, frames=0, fps=0;
const JPEG_BUDGET_BPS=5000000, JPEG_MAX_FPS=5;
const loopbackHost=['localhost','127.0.0.1','[::1]','::1'].includes(location.hostname || location.host.split(':')[0]);
const wsURL = path => `${location.protocol==='https:'?'wss:':'ws:'}//${location.host}${path}`;
const notice = message => {$('notice').textContent=message;};
function setControl(active){
  const gained=active&&!controlling;
  controlling=active;
  for(const surface of [screen,liveVideo])surface.classList.toggle('controlling',active);
  $('take').hidden=active; $('shortcut').disabled=!active; $('release').disabled=!active;
  if(active){if(gained)display.focus({preventScroll:true});$('mode-label').textContent='手动控制';}
}
function send(message){
  if(!input || input.readyState!==WebSocket.OPEN)return;
  if(queue.length>100){queue=[];input.close();notice('输入队列已重置，请重新接管');return;}
  // Only merge consecutive hover moves; preserve down/up/drag transitions.
  const previous=queue.at(-1);
  if(message.type==='pointer' && message.motion && previous?.motion && previous.buttons===message.buttons)
    queue[queue.length-1]=message;
  else queue.push(message);
  pump();
}
function pump(){if(busy || !queue.length || input?.readyState!==WebSocket.OPEN)return;busy=true;input.send(JSON.stringify(queue.shift()));}
function connectInput(){
  input=new WebSocket(wsURL('/ws/control'));busy=false;queue=[];
  input.onmessage=event=>{const reply=JSON.parse(event.data);busy=false;setControl(reply.controlling);
    if(reply.error)notice(reply.error);pump();};
  input.onclose=()=>{setControl(false);busy=false;queue=[];setTimeout(connectInput,1500);};
  input.onerror=()=>input.close();
}
function connectVideo(){
  if(rtcActive || (video && video.readyState<2))return;
  const socket=new WebSocket(wsURL('/ws/video'));video=socket;socket.binaryType='arraybuffer';
  let requestedAt=0;
  const requestNext=()=>{if(socket===video && !rtcActive && socket.readyState===WebSocket.OPEN){
    requestedAt=performance.now();socket.send('next');}};
  socket.onopen=requestNext;
  socket.onmessage=async event=>{
    try{
      if(typeof event.data!=='string' && socket===video && !rtcActive){
        const bitmap=await createImageBitmap(new Blob([event.data],{type:'image/jpeg'}));
        if(socket!==video || rtcActive){bitmap.close();return;}
        if(screen.width!==bitmap.width || screen.height!==bitmap.height){screen.width=bitmap.width;screen.height=bitmap.height;}
        ctx.drawImage(bitmap,0,0);bitmap.close();lastFrame=performance.now();frames++;
        display=screen;screen.hidden=false;liveVideo.hidden=true;
        screen.classList.add('live');$('empty').hidden=true;
      }
    }catch(error){notice(`画面解码失败：${error.message}`);}
    const bytes=typeof event.data==='string'?0:event.data.byteLength;
    const interval=Math.max(1000/JPEG_MAX_FPS,bytes*8000/JPEG_BUDGET_BPS);
    setTimeout(requestNext,Math.max(0,interval-(performance.now()-requestedAt)));
  };
  socket.onclose=()=>{if(socket===video){video=null;setTimeout(connectVideo,1500);}};
  socket.onerror=()=>socket.close();
}
function stopJPEG(){const old=video;video=null;if(old){old.onclose=null;old.close();}}
function stopRTC(){
  rtcGeneration++;rtcStarting=false;rtcActive=false;rtcFrames=0;previousRtcStats=null;rtcRtt=null;
  const old=peer;peer=null;if(old){old.onconnectionstatechange=null;old.close();}
  if(rtcId){fetch(`/api/rtc/${rtcId}`,{method:'DELETE',keepalive:true}).catch(()=>{});rtcId=null;}
  liveVideo.srcObject=null;liveVideo.hidden=true;display=screen;screen.hidden=false;
}
function showRTCDiagnostic(){
  $('diagnostics').textContent=JSON.stringify({server:lastState?.video?.error||'',rtc:rtcDiagnostic,
    lastFailure:lastRTCFailure},null,2);
}
function failureReason(){
  if(rtcDiagnostic.connection!=='connected')return `WebRTC 媒体连接未建立（ICE: ${rtcDiagnostic.ice||'未知'}），尚未进入解码`;
  if(!(rtcDiagnostic.bytesReceived>0))return 'WebRTC 已连接，但没有收到视频数据';
  if(!(rtcDiagnostic.framesDecoded>0))return `已收到视频，但 H.264 解码帧数为 0（${rtcDiagnostic.codec||'编解码器待确认'}）`;
  return '视频已经解码，但页面未成功播放或绘制';
}
function rtcBecameActive(pc){
  if(pc!==peer)return;
  if(!rtcActive){rtcActive=true;stopJPEG();if(controlling)liveVideo.focus({preventScroll:true});
    notice('');reportRTC('playing');}
  liveVideo.classList.add('live');liveVideo.hidden=false;screen.hidden=true;display=liveVideo;
  $('empty').hidden=true;
}
function reportRTC(reason){
  const payload={reason,diagnostic:{...rtcDiagnostic}};
  fetch('/api/rtc/report',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload),keepalive:true}).catch(()=>{});
}
function rtcFailed(message){
  lastRTCFailure={message,...rtcDiagnostic};reportRTC(message);showRTCDiagnostic();
  stopRTC();rtcSuppressed=true;connectVideo();
  notice(`${message}，使用兼容画面。需要重试时可重新选择“自动 · WebRTC”。`);
}
async function startRTC(){
  if(peer || rtcStarting || preferJPEG || rtcSuppressed || performance.now()<rtcRetryAt)return;
  if(typeof RTCPeerConnection==='undefined'){
    rtcDiagnostic={stage:'浏览器未提供 RTCPeerConnection'};
    rtcRetryAt=performance.now()+60000;
    notice('此浏览器环境未提供 WebRTC，当前为 JPEG 兼容画面。');showRTCDiagnostic();return;
  }
  rtcStarting=true;const generation=++rtcGeneration;
  rtcDiagnostic={stage:'创建连接',connection:'new',ice:'new',bytesReceived:0,framesDecoded:0};
  const pc=new RTCPeerConnection({iceServers:[]});peer=pc;
  pc.addTransceiver('video',{direction:'recvonly'});
  pc.onconnectionstatechange=()=>{
    if(pc===peer){rtcDiagnostic.connection=pc.connectionState;rtcDiagnostic.ice=pc.iceConnectionState;showRTCDiagnostic();}
    if(pc===peer && ['failed','disconnected'].includes(pc.connectionState))rtcFailed('实时视频连接中断');
  };
  pc.oniceconnectionstatechange=()=>{if(pc===peer){rtcDiagnostic.ice=pc.iceConnectionState;showRTCDiagnostic();}};
  pc.ontrack=event=>{
    if(pc!==peer)return;
    // A track notification only confirms SDP negotiation, not usable media.
    // Keep the JPEG stream visible until a real frame is decoded or rendered.
    const receiver=event.receiver;
    // These experimental properties must not prevent playback on other browsers.
    try{if('jitterBufferTarget' in receiver)receiver.jitterBufferTarget=0;}catch(_){}
    try{if('playoutDelayHint' in receiver)receiver.playoutDelayHint=0;}catch(_){}
    liveVideo.srcObject=event.streams[0] || new MediaStream([event.track]);
    liveVideo.onloadeddata=()=>{if(pc===peer && liveVideo.videoWidth>0){rtcBecameActive(pc);lastFrame=performance.now();}};
    liveVideo.onplaying=()=>{if(pc===peer && liveVideo.videoWidth>0){rtcBecameActive(pc);lastFrame=performance.now();}};
    liveVideo.onerror=()=>{if(pc===peer)rtcFailed(`浏览器播放失败：${liveVideo.error?.message||liveVideo.error?.code||'未知错误'}`);};
    liveVideo.play().catch(error=>{if(pc===peer)rtcFailed(`浏览器拒绝播放：${error.message}`);});
    const rendered=()=>{
      if(pc!==peer)return;
      rtcBecameActive(pc);
      lastFrame=performance.now();frames++;liveVideo.classList.add('live');$('empty').hidden=true;
      if(liveVideo.requestVideoFrameCallback)liveVideo.requestVideoFrameCallback(rendered);
    };
    if(liveVideo.requestVideoFrameCallback)liveVideo.requestVideoFrameCallback(rendered);
  };
  try{
    rtcDiagnostic.stage='收集网络候选';
    await pc.setLocalDescription(await pc.createOffer());
    if(pc.iceGatheringState!=='complete')await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('网络候选收集超时')),5000);
      pc.onicegatheringstatechange=()=>{if(pc.iceGatheringState==='complete'){clearTimeout(timer);resolve();}};
    });
    rtcDiagnostic.stage='协商 H.264';
    const response=await fetch('/api/rtc/offer',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sdp:pc.localDescription.sdp,type:'offer'}),signal:AbortSignal.timeout(12000)});
    const answer=await response.json();
    if(!response.ok)throw new Error(answer.error || '视频协商失败');
    if(generation!==rtcGeneration){fetch(`/api/rtc/${answer.id}`,{method:'DELETE'}).catch(()=>{});return;}
    rtcId=answer.id;await pc.setRemoteDescription({sdp:answer.sdp,type:answer.type});rtcStarting=false;
    rtcDiagnostic.stage='等待视频';showRTCDiagnostic();
    setTimeout(async()=>{if(pc===peer && !rtcActive){await rtcStats();if(pc===peer && !rtcActive)rtcFailed(failureReason());}},12000);
  }catch(error){if(generation===rtcGeneration)rtcFailed(error.message);}
}
async function rtcStats(){
  const pc=peer;
  if(!pc)return;
  try{
    const stats=await pc.getStats();
    if(pc!==peer)return;
    rtcDiagnostic.connection=pc.connectionState;rtcDiagnostic.ice=pc.iceConnectionState;
    stats.forEach(stat=>{
      if(stat.type==='inbound-rtp' && (stat.kind==='video'||stat.mediaType==='video')){
        const codec=stats.get(stat.codecId);
        Object.assign(rtcDiagnostic,{bytesReceived:stat.bytesReceived||0,packetsReceived:stat.packetsReceived||0,
          packetsLost:stat.packetsLost||0,framesDecoded:stat.framesDecoded||0,
          keyFramesDecoded:stat.keyFramesDecoded||0,framesDropped:stat.framesDropped||0,
          codec:codec?.mimeType,codecParameters:codec?.sdpFmtpLine,
          decoder:stat.decoderImplementation,hardwareDecoder:stat.powerEfficientDecoder,
          videoWidth:liveVideo.videoWidth,readyState:liveVideo.readyState,paused:liveVideo.paused});
        if(previousRtcStats){
          rtcFrames=Math.max(0,Math.round((stat.framesDecoded-previousRtcStats.framesDecoded)*1000/(stat.timestamp-previousRtcStats.timestamp)));
          if(rtcFrames){lastFrame=performance.now();rtcBecameActive(pc);}
        }
        previousRtcStats=stat;
      }
      if(stat.type==='candidate-pair' && stat.state==='succeeded' && stat.currentRoundTripTime!==undefined)
        rtcRtt=Math.round(stat.currentRoundTripTime*1000);
    });
    showRTCDiagnostic();
  }catch(_){}
}
async function refresh(){
  try{
    const response=await fetch('/api/status',{cache:'no-store'});
    if(response.status===401){location.reload();return;}
    if(!response.ok)throw new Error('状态查询失败');
    const state=await response.json();lastState=state;
    if(state.video.transport==='webrtc' && !preferJPEG)startRTC();
    $('connection-dot').classList.add('online');$('connection-label').textContent='控制台已连接';
    $('video-status').textContent=state.video.signal?'视频 · 有信号':'视频 · 无信号';
    $('video-status').classList.toggle('good',state.video.signal);
    const input=state.input.connected;$('input-status').textContent=input?'输入 · 已连接':'输入 · 未连接';
    $('input-status').classList.toggle('good',input);
    $('take').disabled=!input || (state.control.connected&&!controlling);
    $('give').hidden=!state.control.manual;
    $('give').disabled=state.control.connected&&!controlling;
    if(!controlling)$('mode-label').textContent=state.control.manual?'仅查看 · MCP 已暂停':'仅查看';
    $('hint').textContent=controlling?'正在手动控制，MCP 输入已暂停。切换窗口时会自动释放按键。':
      state.control.manual?'另一个浏览器正在控制，MCP 输入暂停。连接断开后自动恢复。':'默认仅查看。接管后点击画面，即可使用鼠标和键盘。';
    $('encoder-label').textContent=rtcActive?'H.264 · WebRTC':'JPEG · 逐帧画面';
    $('video-info').textContent=state.video.signal?`${state.video.width} × ${state.video.height} · ${state.video.test_source?'测试画面 · ':''}${rtcActive?'H.264 / WebRTC':'JPEG'+(state.video.transport==='webrtc'?' · 兼容画面':'')}${rtcActive&&rtcRtt!==null?' · 链路往返 '+rtcRtt+' ms':''}`:'等待视频信号';
    showRTCDiagnostic();
    if(!state.video.signal || performance.now()-lastFrame>3000){
      screen.classList.remove('live');
      if(!peer || !state.video.signal)liveVideo.classList.remove('live');
      $('empty').hidden=false;
      if(rtcActive && performance.now()-lastFrame>5000)rtcFailed('视频帧接收超时');
      $('empty-title').textContent=state.video.signal?'正在接收画面':'等待视频信号';
      $('empty-message').textContent=state.video.signal?'画面通道正在恢复连接':'请检查被控设备的视频输出和连接';
    }
  }catch(error){$('connection-dot').classList.remove('online');$('connection-label').textContent='连接中断';
    screen.classList.remove('live');$('empty').hidden=false;$('empty-title').textContent='正在重新连接';}
  setTimeout(refresh,1500);
}
$('take').onclick=()=>{notice('');send({type:'take'});};
$('give').onclick=()=>{notice('');send({type:'give'});};
$('release').onclick=()=>send({type:'release'});
$('transport').onchange=event=>{preferJPEG=event.target.value==='jpeg';stopRTC();connectVideo();if(!preferJPEG){rtcSuppressed=false;rtcRetryAt=0;startRTC();}};
$('shortcut').onchange=event=>{if(event.target.value)send({type:'shortcut',name:event.target.value});event.target.value='';};
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('screen-shell').requestFullscreen();}catch(e){notice(e.message);}};
$('logout').onclick=async()=>{input?.close();stopJPEG();stopRTC();await fetch('/logout',{method:'POST'});location.reload();};
function pointer(event, wheel=0, motion=false){
  if(!controlling)return;event.preventDefault();const rect=display.getBoundingClientRect();
  send({type:'pointer',x:Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),
    y:Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height)),buttons:event.buttons&7,wheel,motion});
}
for(const surface of [screen,liveVideo]){
surface.addEventListener('pointerdown',event=>{if(controlling){surface.focus();surface.setPointerCapture(event.pointerId);pointer(event);}});
surface.addEventListener('pointerup',event=>pointer(event));
surface.addEventListener('pointermove',event=>pointer(event,0,true));
surface.addEventListener('pointercancel',()=>{if(controlling)send({type:'release'});});
surface.addEventListener('wheel',event=>pointer(event,-Math.sign(event.deltaY)),{passive:false});
surface.addEventListener('contextmenu',event=>event.preventDefault());
for(const type of ['keydown','keyup'])surface.addEventListener(type,event=>{
  if(!controlling)return;event.preventDefault();event.stopPropagation();
  // Host repeats held keys itself, avoiding duplicate browser repeat events.
  if(event.repeat)return;send({type:'key',code:event.code,down:type==='keydown'});
});
surface.addEventListener('blur',release);
}
function release(){if(controlling)send({type:'release'});}
window.addEventListener('blur',release);
document.addEventListener('visibilitychange',()=>{if(document.hidden)release();});
window.addEventListener('pagehide',()=>{input?.close();stopJPEG();stopRTC();});
setInterval(()=>{if(!busy && !queue.length)send({type:'ping'});},2000);
setInterval(()=>{fps=frames;frames=0;$('rate').textContent=`${rtcActive?rtcFrames:fps} fps`;rtcStats();},1000);
if(loopbackHost){preferJPEG=true;$('transport').value='jpeg';notice('本地转发地址默认使用兼容画面，适用于 SSH 隧道。');}
setControl(false);connectInput();connectVideo();refresh();
