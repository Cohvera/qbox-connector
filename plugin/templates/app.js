'use strict';
const $ = id => document.getElementById(id);
const form = $('config');
let csrf = '', dirty = false, ready = false, posting = false;
const when = value => value ? new Date(value * 1000).toLocaleString('nl-BE') : 'Nog niet';
async function api(body) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(body ? location.pathname : '?status', {method: body ? 'POST' : 'GET', credentials:'same-origin', cache:'no-store', signal:controller.signal, ...(body ? {headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,csrf})} : {})});
    const data = await response.json();
    if (!response.ok) throw Error(data.message || 'Aanvraag mislukt.');
    return data;
  } finally {clearTimeout(timer);}
}
function message(text, error=false){$('message').hidden=false;$('message').textContent=text;$('message').className=error?'error':'success';}
function populate(settings){for(const [name,value] of Object.entries(settings)){const field=form.elements.namedItem(name);if(!field)continue;if(field.type==='checkbox')field.checked=value===true||value==='1';else field.value=value;}form.elements.QBOX_IMPORT_API_KEY.value='';}
async function refresh(){
  try {
    const data=await api();csrf=data.csrf;
    if(!ready){populate(data.settings);ready=true;$('fields').disabled=false;}
    $('key-note').textContent=data.settings.has_key?'Sleutel opgeslagen. Laat leeg om die te behouden.':'Er is nog geen sleutel opgeslagen.';
    const s=data.status;
    $('state').textContent=!s.worker_online?'Service niet bereikbaar':s.busy?'Bezig':data.settings.enabled?'Actief':'Gepauzeerd';
    $('activity').textContent=s.busy?(s.action==='test'?'Bron en Cohvera controleren…':'Gegevens synchroniseren…'):s.last_error?'Laatste poging mislukt':'Klaar voor de volgende controle';
    $('last').textContent=when(s.last_success);$('next').textContent=data.settings.enabled?when(s.next_run):'Automatisch uit';
    $('count').textContent=s.project_count!=null?`${s.project_count} projecten${s.company?' · '+s.company:''}`:'Nog geen aantal bevestigd';
    $('result').textContent=s.message||'Nog geen controle uitgevoerd.';$('error').hidden=!(s.last_error||s.tv_warning);$('error').textContent=s.last_error||s.tv_warning||'';
    $('test').disabled=$('sync').disabled=posting||dirty||!s.worker_online||s.busy||s.queued||!data.settings.has_key;
  }catch{message('De plugin is niet bereikbaar. Vernieuw de pagina of controleer je LoxBerry-aanmelding.',true);$('test').disabled=$('sync').disabled=true;}
}
form.addEventListener('input',()=>{dirty=true;$('dirty').hidden=false;$('test').disabled=$('sync').disabled=true;});
async function action(body){if(posting)return;posting=true;$('save').disabled=$('test').disabled=$('sync').disabled=true;
 try{const data=await api(body);message(data.message);if(body.action==='save'){dirty=false;$('dirty').hidden=true;form.elements.QBOX_IMPORT_API_KEY.value='';}}catch(e){message(e.message||'Aanvraag mislukt.',true);}finally{posting=false;$('save').disabled=false;await refresh();}}
form.addEventListener('submit',e=>{e.preventDefault();const settings=Object.fromEntries(new FormData(form));settings.enabled=form.elements.enabled.checked;settings.QBOX_TV_ENABLED=form.elements.QBOX_TV_ENABLED.checked?'1':'0';void action({action:'save',settings});});
$('test').addEventListener('click',()=>void action({action:'test'}));$('sync').addEventListener('click',()=>void action({action:'sync'}));
async function poll(){await refresh();setTimeout(poll,4000);}void poll();
