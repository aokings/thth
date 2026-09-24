import {AtomicObject} from './approval-object.js';
import {fail,fields,opaque,equal,unb64,verifier,personStub,PERSON} from './approval.js';
import {STATE_PATTERN,relayStub} from './relay.js';

// 招待（設計 3.10.0）。1 本の招待に 1 つの object（名前は code の SHA-256）。
// ここに置くのは hash で引ける状態・期限・VM から返った認可 URL（10 分で消す）だけ。
// code そのもの・token・承認 secret は置かない。
export const STALL_MS=120_000;        // 押してから 2 分進まなければ「運営者に連絡を」
export const AUTH_MS=600_000;         // 認可 URL の寿命（VM の session と同じ 10 分）
export const MAX_MS=90*86_400_000;    // 招待の期限は最長 90 日
const SKEW_MS=60_000;
export const RESET_REASONS=['auth_expired','auth_failed','account_exists','unavailable'];
const SCOPE=/^threads_[a-z_]{1,40}$/;
const HANDLE=/^[A-Za-z0-9_.]{1,64}$/;
// VM が組む認可 URL は Threads の authorize だけ。ほかの行き先へは送らない。
const AUTHORIZE=/^https:\/\/(?:www\.)?threads\.(?:net|com)\/oauth\/authorize\?[^\s"'<>\\]*$/;

export function validAuthorize(url){
  if(typeof url!=='string'||url.length>4096||!AUTHORIZE.test(url))return null;
  try{const parsed=new URL(url);const states=parsed.searchParams.getAll('state');
    return states.length===1&&STATE_PATTERN.test(states[0])?states[0]:null;}catch{return null;}
}

export class InviteObject extends AtomicObject {
  // 期限を過ぎた招待は、どの状態でも「使えない」（取り消し・表示済みはそのまま言う）。
  row(){
    const row=this.ctx.storage.kv.get('invite');
    if(row&&this.now()>=row.expires_at&&!['revoked','done'].includes(row.status))return {...row,status:'expired'};
    return row;
  }
  // 認可 URL は 10 分で消える（読むときも消えたものとして扱う）。
  url(row){return row?.authorize_url&&this.now()<row.authorize_expires_at?row.authorize_url:null;}
  async schedule(){
    const row=this.ctx.storage.kv.get('invite');if(!row)return;
    const next=row.authorize_url&&row.authorize_expires_at<row.expires_at?row.authorize_expires_at:row.expires_at;
    await this.ctx.storage.setAlarm(next);
  }
  async alarm(){
    const row=this.ctx.storage.kv.get('invite');
    if(!row||this.now()>=row.expires_at){await this.ctx.storage.deleteAll();return;}
    if(row.authorize_url&&this.now()>=row.authorize_expires_at)
      this.ctx.storage.transactionSync(()=>this.put('invite',{...row,authorize_url:null}));
    await this.schedule();
  }
  async manage(operation,body,ticket,subject){
    let marker=null;
    const result=this.atomic(()=>{
      if(!['create','status','authorize','reset','complete','revoke'].includes(operation))return fail();
      if(!body||typeof body!=='object'||Array.isArray(body)||typeof subject!=='string'||!/^[a-f0-9]{64}$/.test(subject))return fail();
      if(!this.replay(ticket))return fail(409,'replayed_request');
      const now=this.now(),row=this.row();
      if(operation==='create'){
        if(!fields(body,['media','production','expires_at','scopes'])||body.media!=='threads'||typeof body.production!=='boolean'||
           !Number.isSafeInteger(body.expires_at)||body.expires_at<=now||body.expires_at>now+MAX_MS+SKEW_MS||
           !Array.isArray(body.scopes)||body.scopes.length<1||body.scopes.length>16||!body.scopes.every(s=>typeof s==='string'&&SCOPE.test(s)))return fail();
        if(this.ctx.storage.kv.get('invite'))return fail(409,'invite_exists');
        this.put('invite',{key:subject,status:'open',media:body.media,production:body.production,scopes:body.scopes,expires_at:body.expires_at,
          csrf:opaque(),created_at:now,clicked_at:null,authorize_url:null,authorize_expires_at:null,reason:null,
          person:null,account:null,handle:null});
        return {status:200,body:{status:'open'}};
      }
      if(!row)return fail(404,'not_found');
      if(operation==='status'){
        if(!fields(body,[]))return fail();
        return {status:200,body:{status:row.status,expires_at:row.expires_at,clicked_at:row.clicked_at,
          authorize_expires_at:row.status==='authorizing'?row.authorize_expires_at:null}};
      }
      if(operation==='revoke'){
        if(!fields(body,[]))return fail();
        // 口座の用意が済んだ招待は取り消さない（止めるのは VM の thth account leave）。
        if(['ready','done'].includes(row.status))return fail(409,'invite_used');
        this.put('invite',{...row,status:'revoked',authorize_url:null});return {status:200,body:{status:'revoked'}};
      }
      if(row.status==='expired'||row.status==='revoked')return fail(410,'invite_unavailable');
      if(operation==='authorize'){
        const state=fields(body,['authorize_url','expires_at'])?validAuthorize(body.authorize_url):null;
        if(!state||!Number.isSafeInteger(body.expires_at)||body.expires_at<=now||body.expires_at>now+AUTH_MS+SKEW_MS)return fail();
        if(row.status!=='clicked')return fail(409,'not_clicked');
        this.put('invite',{...row,status:'authorizing',authorize_url:body.authorize_url,authorize_expires_at:body.expires_at,reason:null});
        marker=state;return {status:200,body:{status:'authorizing'}};
      }
      if(operation==='reset'){
        if(!fields(body,['reason'])||!RESET_REASONS.includes(body.reason))return fail();
        if(row.status==='open')return {status:200,body:{status:'open'}};
        if(!['clicked','authorizing'].includes(row.status))return fail(409,'not_resettable');
        this.put('invite',{...row,status:'open',clicked_at:null,authorize_url:null,authorize_expires_at:null,reason:body.reason});
        return {status:200,body:{status:'open'}};
      }
      // complete: VM が口座を用意した。承認 secret はまだ作らない（本人が押したときに 1 回だけ）。
      if(!fields(body,['person','account','handle'])||typeof body.person!=='string'||typeof body.account!=='string'||
         !PERSON.test(body.person)||!PERSON.test(body.account)||typeof body.handle!=='string'||!HANDLE.test(body.handle))return fail();
      if(row.status==='ready'&&row.person===body.person&&row.account===body.account&&row.handle===body.handle)
        return {status:200,body:{status:'ready'}};
      if(!['clicked','authorizing'].includes(row.status))return fail(409,'not_authorizing');
      this.put('invite',{...row,status:'ready',person:body.person,account:body.account,handle:body.handle,
        authorize_url:null,authorize_expires_at:null,reason:null});
      return {status:200,body:{status:'ready'}};
    });
    if(result.status===200&&operation!=='status'){
      try{await this.schedule();}catch{}
      // 預かり所の戻り（/callback/）で「招待のタブに戻って」と言えるように印を付ける。
      // 印が付かなくても認可は進む（表示の言葉が変わるだけ）。
      if(marker){try{await (await relayStub(this.env,marker)).markInvite();}catch{}}
    }
    return result;
  }
  view(){
    const row=this.row();if(!row)return {status:'missing'};
    const {status,media,production,scopes,expires_at,csrf,clicked_at,reason,account,handle}=row;
    const stalled=status==='clicked'&&clicked_at!==null&&this.now()-clicked_at>=STALL_MS;
    return {status,media,production,scopes,expires_at,csrf,reason,account,handle,stalled,authorize_url:status==='authorizing'?this.url(row):null};
  }
  // 本人が「Threads で認可する」を押した。VM の常駐が clicked を拾う。
  start(csrf){return this.atomic(()=>{
    const row=this.row();if(!row)return fail(410,'invite_unavailable');
    if(typeof csrf!=='string'||!STATE_PATTERN.test(csrf)||!equal(unb64(csrf),unb64(row.csrf)))return fail(403,'forbidden');
    if(['clicked','authorizing'].includes(row.status))return {status:200};
    if(row.status!=='open')return fail(410,'invite_unavailable');
    this.put('invite',{...row,status:'clicked',clicked_at:this.now(),reason:null});return {status:200};
  });}
  // 承認 secret を 1 回だけ作って見せる。secret はこの要求の中にしか無い（保存するのは
  // ApprovalPerson の verifier だけ）。ready → done は 1 回きり。done の招待は二度と secret を出さない。
  async reveal(csrf){
    const claim=this.atomic(()=>{
      const row=this.row();if(!row)return fail(410,'invite_unavailable');
      if(typeof csrf!=='string'||!STATE_PATTERN.test(csrf)||!equal(unb64(csrf),unb64(row.csrf)))return fail(403,'forbidden');
      if(row.status!=='ready')return fail(410,'invite_unavailable');
      // 同時の 2 回目の押下は、1 回目が終わるまで通さない（secret を 2 つ作らない）。
      if(row.revealing_at&&this.now()-row.revealing_at<60_000)return fail(409,'revealing');
      this.put('invite',{...row,revealing_at:this.now()});return {status:200,body:{person:row.person,key:row.key}};
    });
    if(claim.status!==200)return claim;
    const secret=opaque(),salt=opaque();let ok=false;
    try{ok=(await (await personStub(this.env,claim.body.person)).provision(salt,await verifier(secret,salt),claim.body.key)).status===200;}catch{}
    return this.atomic(()=>{
      const latest=this.ctx.storage.kv.get('invite');
      if(!latest)return fail(410,'invite_unavailable');
      if(!ok){this.put('invite',{...latest,revealing_at:null});return fail(503,'reveal_unavailable');}
      this.put('invite',{...latest,status:'done',revealing_at:null,revealed_at:this.now()});
      return {status:200,body:{secret,person:claim.body.person}};
    });
  }
}
