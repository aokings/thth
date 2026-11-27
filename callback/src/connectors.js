// 持ち主がコネクタ（リモート MCP・段 K）を見て取り消す頁 `/connectors`。
//
//   GET  /connectors                         持ち主の名前と secret の form
//   POST /connectors {owner, secret}          その持ち主のコネクタの一覧（名前・レーン・括り・範囲・日時。鍵も token も無い）
//   POST /connectors {owner, secret, device}  1 つ取り消す: Worker の預かりを消し、VM のレーンの鍵の取り消しを置く
//
// session を持たない（cookie を作らない）。どの POST も secret を確かめる（失敗の数え方は /activity と同じ・5 回で 15 分）。
// 作法は login と同じ（script なし・CSP・no-store・no-referrer・同一 origin の POST）。
import {PERSON,boundedBody,personStub,fromSameOrigin,page,t,escape,publicQuota} from './person.js';
import {reply} from './relay.js';
import {connectorStub} from './oauth.js';
import {DEVICE_ID} from './connector-object.js';

const TITLE='THTH コネクタ';
const ownerFields=(owner='')=>`<label>${t('持ち主の名前','Owner name')} <input name="owner" autocomplete="username" required maxlength="64" value="${escape(owner)}"></label><label>${t('持ち主のシークレット','Owner secret')} <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label>`;
function signIn(status=200,note=''){
  return page(status,`<h1>${t('AI のコネクタ','AI connectors')}</h1>${note}
<p>${t('claude.ai などに許したコネクタ（リモート MCP）を見て、取り消せます。取り消すと次の呼び出しから使えず、サーバーのレーンの鍵も数十秒で止まります。','See the connectors (remote MCP) you allowed for claude.ai and others, and revoke them. A revoked connector stops at the next call, and the server stops its lane key within a few tens of seconds.')}</p>
<form method="post" action="/connectors">${ownerFields()}<button type="submit">${t('見る','Show')}</button></form>`,TITLE);
}
const failed=()=>signIn(403,`<p class="err">${t('確かめられませんでした。持ち主の名前とシークレットを確かめてください。5 回続けて間違えると 15 分閉じます。','Could not confirm. Check the owner name and the secret. After five failures in a row it is closed for 15 minutes.')}</p>`);
const when=ms=>escape(new Date(ms).toISOString().replace(/\.\d+Z$/,'Z'));
function listPage(owner,rows){
  const items=rows.length?`<ul>${rows.map(r=>`<li><p><strong>${escape(r.client_name)}</strong> · ${t('レーン','lane')} <code>${escape(r.lane)}</code> · ${t('括り','workspace')} ${escape(r.workspace??t('（結び付け待ち）','(binding)'))} · ${r.writes?t('読む・書く','read and write'):t('読むだけ','read only')}</p>
<p class="note">${t('許した日','Allowed')} ${when(r.created_at)}${r.last_used_at?' · '+t('最後に書いた日','last write')+' '+when(r.last_used_at):''} · ${t('期限','expires')} ${when(r.expires_at)}</p>
<form method="post" action="/connectors"><input type="hidden" name="owner" value="${escape(owner)}"><input type="hidden" name="device" value="${escape(r.device_id)}"><label>${t('持ち主のシークレット','Owner secret')} <input type="password" name="secret" autocomplete="current-password" required maxlength="128"></label><button type="submit" class="quiet">${t('このコネクタを取り消す','Revoke this connector')}</button></form></li>`).join('')}</ul>`
    :`<p class="status">${t('許したコネクタはありません。','No connectors.')}</p>`;
  return page(200,`<h1>${t('AI のコネクタ','AI connectors')}</h1><p class="sub">${t('持ち主','Owner')}: ${escape(owner)}</p>${items}`,TITLE);
}
const revokedPage=vm=>page(200,`<h1>${t('取り消しました','Revoked')}</h1><p class="status">${vm==='placed'?t('次の呼び出しから使えません。サーバーのレーンの鍵も数十秒で止まります。','It stops at the next call. The server stops its lane key within a few tens of seconds.'):t('次の呼び出しから使えません。サーバーのレーンの鍵の取り消しは、頼み直しながら数分の内に届けます。','It stops at the next call. The lane key revocation is retried and reaches the server within a few minutes.')}</p><p><a href="/connectors">${t('一覧に戻る','Back')}</a></p>`,TITLE);

export async function connectorsRequest(request,env,url){
  try{
    if(url.search||url.hash||url.pathname!=='/connectors')return reply(404,{error:'not_found'});
    if(!env.CONNECTOR||!env.PERSON)return reply(503,{error:'connectors_unavailable'});
    if(!await publicQuota(request,env))return reply(429,{error:'rate_limited'});
    if(request.method==='GET')return signIn();
    if(request.method!=='POST')return reply(405,{error:'method_not_allowed'});
    if(!fromSameOrigin(request,url))return reply(403,{error:'forbidden'});
    const form=new URLSearchParams(await boundedBody(request,2048));
    const keys=[...form.keys()].sort().join(',');
    if(keys!=='owner,secret'&&keys!=='device,owner,secret')return reply(400,{error:'invalid_request'});
    const owner=form.get('owner');
    if(!PERSON.test(owner))return failed();
    const device=form.get('device');
    if(device!==null&&!DEVICE_ID.test(device))return reply(400,{error:'invalid_request'});
    let ok;try{ok=await (await personStub(env,owner)).ownerCheck(form.get('secret'));}catch{ok={status:503};}
    if(ok.status===403)return failed();
    if(ok.status!==200)return reply(503,{error:'connectors_unavailable'});
    const stub=connectorStub(env);
    if(device===null){const listed=await stub.list(owner);return listPage(owner,listed.body);}
    const done=await stub.revokeOwned(owner,device);
    if(done.status===404)return page(404,`<h1>${t('見つかりません','Not found')}</h1><p>${t('もう取り消されたか、期限が切れています。','It was already revoked or has expired.')}</p><p><a href="/connectors">${t('一覧に戻る','Back')}</a></p>`,TITLE);
    if(done.status!==200)return reply(503,{error:'connectors_unavailable'});
    return revokedPage(done.body.vm_revoke);
  }catch{return reply(503,{error:'connectors_unavailable'});}
}
