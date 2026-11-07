const $=id=>document.getElementById(id);
let controller=null,places=[],sourceResult=null,connected=false;
const originalEmpty=$('result').innerHTML;
function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function notice(text,error=false){$('notice').textContent=text;$('notice').className='notice'+(error?' error':'');$('notice').hidden=!text;}
async function api(path,options={}){const r=await fetch(path,options),v=await r.json();if(!r.ok)throw new Error(v.error||v.detail||'The request could not be completed.');return v;}
function post(path,body={}){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
function link(label,url,main=false){const a=node('a','link-btn'+(main?' main':''),label);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
const locationText=p=>[p.name,p.address].filter(Boolean).join(', ');
function mapUrl(p){if(p.length===1)return 'https://www.google.com/maps/search/?'+new URLSearchParams({api:'1',query:locationText(p[0])});return 'https://www.google.com/maps/dir/?'+new URLSearchParams({api:'1',origin:locationText(p[0]),destination:locationText(p.at(-1)),travelmode:'walking',...(p.length>2?{waypoints:p.slice(1,-1).map(locationText).join('|')}:{})});}
function routeParts(p,max=10){const pending=[];for(let i=0;i<p.length;i+=max-1){const part=p.slice(i,i+max);pending.push(part);if(i+max>=p.length)break;}const out=[];while(pending.length){const part=pending.shift();if(mapUrl(part).length<=2048)out.push(part);else if(part.length>2){const n=Math.ceil(part.length/2);pending.unshift(part.slice(n-1));pending.unshift(part.slice(0,n));}else{notice('A location is too long for Google Maps. Untick it to generate the route.',true);}}return out;}
function display(){
 if(!sourceResult)return;
 const box=node('article','card map-card'),head=node('div','result-head'),heading=node('div');heading.append(node('h2','section-title',sourceResult.title||'Places from your Reel'),node('p','small',places.length?'Review the places and untick anything you don’t want.':'No identifiable places were found in the available evidence.'));head.append(heading);box.append(head);
 const actions=node('div','map-actions'),selected=places.filter(p=>p.selected);routeParts(selected).forEach((p,i,all)=>actions.append(link(all.length===1?'Open in Google Maps ↗':'Route '+(i+1)+' ↗',mapUrl(p),true)));box.append(actions);
 const list=node('div','details');places.forEach((p,i)=>{const row=node('div','place'),check=node('input');check.type='checkbox';check.checked=p.selected;check.setAttribute('aria-label','Include '+p.name);check.addEventListener('change',()=>{p.selected=check.checked;display();});row.append(check);const body=node('div','place-body'),a=node('a','',p.name+' ↗');a.href=mapUrl([p]);a.target='_blank';a.rel='noopener noreferrer';body.append(a,node('p','',p.address||''),node('small','',p.evidence||''));row.append(body);if(i>0){const up=node('button','reorder','↑');up.setAttribute('aria-label','Move '+p.name+' up');up.addEventListener('click',()=>{[places[i-1],places[i]]=[places[i],places[i-1]];display();});row.append(up);}list.append(row);});box.append(list);
 if(sourceResult.notes)box.append(node('p','notes',sourceResult.notes));
 if(selected.length>5){const d=node('details');d.append(node('summary','','Shorter routes for mobile browsers'));routeParts(selected,5).forEach((p,i)=>d.append(link('Section '+(i+1)+' ↗',mapUrl(p))));box.append(d);}
 const evidence=node('details');evidence.append(node('summary','','What was analyzed'));evidence.append(node('p','notes',(sourceResult.analysis_methods||[]).map(x=>x.replaceAll('_',' ')).join(' · ')));for(const n of sourceResult.sampling_notes||[])evidence.append(node('p','notes',n));box.append(evidence);
 $('result').replaceChildren(box);$('clear').hidden=false;
}
function progress(message){const box=node('div','progress');box.append(node('h3','','Finding your next stop.'),node('p','',message));const bar=node('div','bar');bar.append(node('span'));box.append(bar);$('result').replaceChildren(box);}
async function status(){
 try{const state=await api('/api/status');connected=state.connected;$('signed-in').hidden=!connected;$('signed-out').hidden=connected;$('account').textContent=state.account||'';$('audio').disabled=!state.audio_available;$('audio-note').textContent=state.audio_available?'Speech is processed on this laptop; no transcription API is used.':'Optional: install speech support to include spoken place names.';
 if(!state.ffmpeg_available)notice('Install FFmpeg and ffprobe before processing videos.',true);
 if(connected){const data=await api('/api/models');$('model').replaceChildren(...data.models.map(m=>{const o=node('option','',m.name);o.value=m.id;return o;}));$('model').disabled=false;$('submit').disabled=!state.ffmpeg_available;}else{$('submit').disabled=true;$('model').disabled=true;$('model').replaceChildren(node('option','','Connect ChatGPT first'));}
 }catch(e){notice(e.message,true);$('submit').disabled=true;}
}
$('connect').addEventListener('click',async()=>{$('connect').disabled=true;try{const data=await post('/api/auth/start');window.location.assign(data.authorize_url);}catch(e){notice(e.message,true);$('connect').disabled=false;}});
$('disconnect').addEventListener('click',async()=>{try{const data=await post('/api/auth/disconnect');notice(data.remote_revocation_confirmed?'Disconnected from ChatGPT.':'Disconnected locally. Remote revocation could not be confirmed; you can also revoke this app in ChatGPT Settings.',!data.remote_revocation_confirmed);await status();}catch(e){notice(e.message,true);}});
$('form').addEventListener('submit',async event=>{
 event.preventDefault();if(controller)return;controller=new AbortController();$('submit').disabled=true;$('disconnect').disabled=true;$('cancel').hidden=false;notice('');sourceResult=null;places=[];$('clear').hidden=true;progress('Starting…');let done=false;
 try{
  const r=await fetch('/api/v1/map',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify({url:$('url').value,model:$('model').value,include_audio:$('audio').checked})});
  if(!r.ok){const v=await r.json();throw new Error(v.error||'Could not start analysis.');}
  const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='';
  while(true){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true});let end;
   while((end=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,end);buffer=buffer.slice(end+2);const kind=block.split('\n').find(l=>l.startsWith('event: '))?.slice(7),raw=block.split('\n').filter(l=>l.startsWith('data: ')).map(l=>l.slice(6)).join('\n');if(!raw)continue;const data=JSON.parse(raw);
    if(kind==='progress')progress(data.message);
    if(kind==='result'){sourceResult=data;places=data.places.map(p=>({...p,selected:true}));display();done=true;}
    if(kind==='error')throw new Error(data.error);
   }
  }
  if(!done)throw new Error('The connection ended before the analysis completed.');
 }catch(e){$('result').innerHTML=originalEmpty;notice(e.name==='AbortError'?'Processing cancelled. Temporary media is being cleaned up.':e.message,e.name!=='AbortError');}
 finally{controller=null;$('submit').disabled=!connected;$('disconnect').disabled=false;$('cancel').hidden=true;}
});
$('cancel').addEventListener('click',()=>controller?.abort());
$('clear').addEventListener('click',()=>{sourceResult=null;places=[];$('result').innerHTML=originalEmpty;$('clear').hidden=true;notice('');});
document.querySelectorAll('[data-url]').forEach(b=>b.addEventListener('click',()=>{$('url').value=b.dataset.url;$('url').focus();}));
const error=new URLSearchParams(location.search).get('auth_error');if(error){notice(error,true);history.replaceState(null,'',location.pathname);}
status();
