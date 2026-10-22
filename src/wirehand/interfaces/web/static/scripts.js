/* Script library UI. Device viewing/input remain in app.js. */
(() => {
  const byId = id => document.getElementById(id);
  let scripts = [], manual = false, running = false, busy = false, fingerprint = '';
  const labels = {idle:'尚未运行',running:'正在执行',stopping:'正在停止',completed:'执行完成',stopped:'已停止',failed:'执行失败'};
  const message = text => {byId('library-notice').textContent = text;};
  async function api(path, options={}) {
    const response = await fetch(path, {cache:'no-store', ...options});
    if(response.status===401){location.reload();throw new Error('请重新登录');}
    const data = await response.json();
    if(!response.ok){const error=new Error(data.error || '操作失败');error.data=data;error.status=response.status;throw error;}
    return data;
  }
  function button(text, action, disabled=false) {
    const item = document.createElement('button');item.textContent=text;item.disabled=disabled;
    item.onclick=()=>perform(action);return item;
  }
  async function perform(action) {
    if(busy)return;
    busy=true;message('');
    try{await action();await refresh();}catch(error){message(error.message);}
    finally{busy=false;}
  }
  function render() {
    const key=JSON.stringify([scripts,manual,running]);
    if(key===fingerprint)return;
    fingerprint=key;
    const list=byId('script-list');list.replaceChildren();
    byId('script-count').textContent=`${scripts.length} 个脚本`;
    byId('scripts-empty').hidden=scripts.length>0;
    for(const script of scripts) {
      const row=document.createElement('article');row.className='script-card';
      const summary=document.createElement('div');summary.className='script-summary';
      const title=document.createElement('strong');title.textContent=script.name;
      const meta=document.createElement('span');
      const state={complete:'可执行',recording:'录制中',interrupted:'录制中断',failed:'录制失败'}[script.status] || script.status;
      meta.textContent=`${state} · ${script.steps} 步 · ${new Date(script.created_at).toLocaleString()}`;
      summary.append(title,meta);
      const actions=document.createElement('div');actions.className='script-actions';
      const run=button('运行',async()=>{
        const repeat=byId('repeat-forever').checked?0:Number(byId('repeat-count').value);
        if(!Number.isInteger(repeat) || repeat<0 || repeat>100000 || (!byId('repeat-forever').checked && repeat===0))throw new Error('请输入 1–100000 的重复次数');
        const state=await api(`/api/scripts/${script.id}/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({repeat})});
        updateRun(state);message(`已开始执行「${script.name}」`);
      },manual || running || !script.runnable);run.className='primary';
      const download=document.createElement('a');download.className='button-link';download.textContent='下载';download.href=`/api/scripts/${script.id}/download`;
      const rename=button('重命名',async()=>{
        const name=window.prompt('脚本名称（也是下载压缩包的名称）',script.name);
        if(name===null)return;
        await api(`/api/scripts/${script.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
      },running || script.status==='recording');
      const remove=button('删除',async()=>{
        if(!window.confirm(`删除「${script.name}」？`))return;
        await api(`/api/scripts/${script.id}`,{method:'DELETE'});
      },running || script.status==='recording');remove.className='quiet';
      actions.append(run,download,rename,remove);row.append(summary,actions);list.append(row);
    }
    byId('run-mode-hint').textContent=manual?'接管模式下不能运行脚本。结束接管后即可执行。':'点击脚本的运行按钮开始执行，关闭页面后仍会继续。';
  }
  function updateRun(run) {
    running=Boolean(run.active);
    byId('stop-script').disabled=!running;
    byId('run-status').textContent=`${labels[run.status] || run.status}${run.name?' · '+run.name:''}${run.repeat===0?' · 无限重复':run.repeat?' · 共 '+run.repeat+' 次':''}`;
    byId('run-progress').textContent=run.name?`已完成 ${run.completed} 次${running?' · 当前第 '+run.iteration+' 次':''}${run.error?' · '+run.error:''}`:'';
    const logs=byId('run-log');
    if(logs.textContent!==run.log){logs.textContent=run.log || '脚本输出将显示在这里';logs.scrollTop=logs.scrollHeight;}
    render();
  }
  async function refresh() {
    const [library,run]=await Promise.all([api('/api/scripts'),api('/api/scripts/run')]);
    scripts=library.scripts;updateRun(run);
  }
  async function upload(file) {
    const query=new URLSearchParams({filename:file.name});
    let result;
    try{result=await api('/api/scripts?'+query,{method:'POST',body:file});}
    catch(error){
      const previous=error.data?.existing;
      if(error.status!==409 || !previous)throw error;
      if(!window.confirm(`已存在「${previous.name}」。是否用上传的压缩包替换？`))return;
      query.set('replace',previous.id);query.set('revision',previous.revision);
      result=await api('/api/scripts?'+query,{method:'POST',body:file});
    }
    message(`已保存「${result.name}」`);
  }
  byId('upload-button').onclick=()=>byId('script-upload').click();
  byId('script-upload').onchange=event=>{const file=event.target.files[0];if(file)perform(()=>upload(file));event.target.value='';};
  byId('repeat-forever').onchange=event=>{byId('repeat-count').disabled=event.target.checked;};
  byId('stop-script').onclick=()=>perform(async()=>updateRun(await api('/api/scripts/stop',{method:'POST'})));
  window.addEventListener('kvm-state',event=>{manual=Boolean(event.detail.control.manual);if(event.detail.run)updateRun(event.detail.run);else render();});
  async function poll(){try{await refresh();}catch(error){message(error.message);}setTimeout(poll,2000);}
  poll();
})();
