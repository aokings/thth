import {mediaRequest} from './media.js';
export {MediaObject} from './media-object.js';
export {DeletionInbox} from './deletion-object.js';
import base from './index.js';
import {approvalRequest,pendingRequest} from './approval.js';
export {AuthRelay} from './relay-object.js';
export {ApprovalPerson,ApprovalSession,ApprovalAccount} from './approval-object.js';
export {InviteObject} from './invite-object.js';
import {inviteRequest} from './invite.js';
export default {fetch(request,env){
  const url=new URL(request.url);
  if(url.pathname.startsWith('/media/')||url.pathname.startsWith('/media-upload/')||url.pathname.startsWith('/m/'))return mediaRequest(request,env,url);
  if(url.pathname.startsWith('/approve/')||url.pathname.startsWith('/approval/'))return approvalRequest(request,env,url);
  if(url.pathname.startsWith('/invite/'))return inviteRequest(request,env,url);
  if(url.pathname==='/pending'||url.pathname.startsWith('/pending/'))return pendingRequest(request,env,url);
  return base.fetch(request,env);
}};
