import {mediaRequest} from './media.js';
export {MediaObject} from './media-object.js';
export {DeletionInbox} from './deletion-object.js';
import base from './index.js';
import {relayRequest} from './person.js';
export {AuthRelay} from './relay-object.js';
export {Person,Account} from './person-object.js';
export {InviteObject} from './invite-object.js';
import {inviteRequest} from './invite.js';
import {activityRequest} from './activity.js';
export default {fetch(request,env){
  const url=new URL(request.url);
  // 3.12.0 §6-2: /activity/ は末尾の / を落として 308。
  if(url.pathname==='/activity/')return new Response(null,{status:308,headers:{location:'/activity','cache-control':'no-store','referrer-policy':'no-referrer'}});
  if(url.pathname.startsWith('/media/')||url.pathname.startsWith('/media-upload/')||url.pathname.startsWith('/m/'))return mediaRequest(request,env,url);
  // 3.13.0: 承認ページ（/approve/…）と承認待ちの一覧（/pending）は無い。
  if(url.pathname.startsWith('/approve/')||url.pathname==='/pending'||url.pathname.startsWith('/pending/'))
    return new Response('Not Found\n',{status:404,headers:{'content-type':'text/plain; charset=utf-8','cache-control':'no-store','referrer-policy':'no-referrer'}});
  // VM→Worker の署名つき relay（3.13.0 で /approval/ から /relay/v/ へ。旧 path は 1 版の間だけ）。
  // 認可の relay（/relay/<state>）より先に見る。
  if(url.pathname.startsWith('/relay/v/')||url.pathname.startsWith('/approval/'))return relayRequest(request,env,url);
  if(url.pathname.startsWith('/invite/'))return inviteRequest(request,env,url);
  if(url.pathname==='/activity'||url.pathname.startsWith('/activity/'))return activityRequest(request,env,url);
  return base.fetch(request,env);
}};
