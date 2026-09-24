/* NexusDesk browser widget. Application secrets must stay on your enterprise server. */
(function (global) {
  'use strict';
  const scriptUrl = document.currentScript && document.currentScript.src;
  global.NexusDeskChat = {
    mount(options) {
      if (!options || !/^[0-9a-f-]{36}$/i.test(options.appId || '')) throw new Error('Valid appId required');
      const base = new URL(options.baseUrl || scriptUrl).origin;
      if (!/^https?:/.test(base)) throw new Error('HTTP(S) platform URL required');
      const frame = document.createElement('iframe');
      frame.title = options.title || 'NexusDesk Chat';
      frame.referrerPolicy = 'strict-origin';
      frame.allow = 'clipboard-write';
      frame.src = base + '/embed/' + options.appId + '?parent_origin=' + encodeURIComponent(location.origin);
      Object.assign(frame.style, {border:'1px solid #e9e7f4',borderRadius:'18px',background:'#fff',width:'400px',height:'min(680px, calc(100dvh - 100px))',maxWidth:'calc(100vw - 32px)',boxShadow:'0 12px 48px #24203822'});
      const host = typeof options.container === 'string' ? document.querySelector(options.container) : options.container;
      let button;
      if (host) { frame.style.width='100%';host.appendChild(frame); }
      else {
        Object.assign(frame.style,{position:'fixed',bottom:'88px',right:'24px',zIndex:'2147483000',display:'none'});
        button=document.createElement('button');button.textContent=options.title||'在线助手';button.type='button';button.setAttribute('aria-expanded','false');
        Object.assign(button.style,{position:'fixed',bottom:'24px',right:'24px',border:'none',borderRadius:'28px',padding:'14px 22px',background:'#6562ff',color:'#fff',cursor:'pointer',zIndex:'2147483001',font:'14px system-ui'});
        button.onclick=()=>{const open=frame.style.display==='none';frame.style.display=open?'block':'none';button.setAttribute('aria-expanded',String(open));};
        document.body.append(frame,button);
      }
      let disposed=false;
      async function sendToken() {
        if(typeof options.getToken !== 'function')return;
        try {
          const result=await options.getToken();
          if(!disposed)frame.contentWindow.postMessage({type:'nexusdesk.token',token:typeof result==='string'?result:result.access_token},base);
        } catch {if(!disposed)frame.contentWindow.postMessage({type:'nexusdesk.token-error'},base);}
      }
      function receive(event) {
        if(event.origin!==base||event.source!==frame.contentWindow)return;
        if(event.data?.type==='nexusdesk.ready'){
          frame.contentWindow.postMessage({type:'nexusdesk.init',agentId:options.agentId||null},base);
          sendToken();
        }
        if(event.data?.type==='nexusdesk.refresh-token')sendToken();
      }
      global.addEventListener('message',receive);
      return {open(){frame.style.display='block';button?.setAttribute('aria-expanded','true');},close(){frame.style.display='none';button?.setAttribute('aria-expanded','false');},destroy(){disposed=true;global.removeEventListener('message',receive);frame.remove();button?.remove();}};
    }
  };
})(window);
