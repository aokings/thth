import {mediaRequest} from './media.js';
export {MediaObject} from './media-object.js';
export {DeletionInbox} from './deletion-object.js';
import base from './index.js';
import {approvalRequest} from './approval.js';
export {AuthRelay} from './relay-object.js';
export {ApprovalPerson,ApprovalAccount} from './approval-object.js';
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
  if(url.pathname.startsWith('/approval/'))return approvalRequest(request,env,url);
  if(url.pathname.startsWith('/invite/'))return inviteRequest(request,env,url);
  if(url.pathname==='/activity'||url.pathname.startsWith('/activity/'))return activityRequest(request,env,url);
  return base.fetch(request,env);
}};
