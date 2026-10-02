import {createViewerGamepadLifecycle, type ViewerGamepadLifecycleOptions} from "./gamepadLifecycle.js";
import {buildViewerGamepadControlMessage, sampleViewerGamepadSnapshot, type ViewerGamepadLike} from "../input/gamepadInput.js";

/** socket/sceneの同一性を非同期処理の開始時に固定する。 */
export type Preparation = {socket: object; epoch: string; generation: number};
export function preparationIsCurrent(captured: Preparation, socket: object|null,
  status: {ticket: {epoch: string}|null; generation: number; busy: string|null}|null): boolean {
  return socket === captured.socket && status?.ticket?.epoch === captured.epoch
    && status.generation === captured.generation && !status.busy;
}
export function canConnect(socket: {readyState: number}|null): boolean {
  return socket === null || (socket.readyState !== 0 && socket.readyState !== 1);
}

/** file読取り中のSTOP・新trial・再接続後に旧importを送らない。未選択epochも比較する。 */
export function conditionReadIsCurrent(capturedSocket:object|null,
  captured:{revision:number;generation:number;ticket:{epoch:string}|null},socket:object|null,
  status:{revision:number;generation:number;ticket:{epoch:string}|null}|null):boolean {
  return capturedSocket===socket && status!==null && captured.revision===status.revision
    && captured.generation===status.generation && captured.ticket?.epoch===status.ticket?.epoch;
}

/** 単一editor要求の応答だけを取り込み、同socketの遅延応答も隔離する。 */
export function editorReplyIsCurrent(pending:{id:string;socket:object|null;status:{revision:number;generation:number;ticket:{epoch:string}|null}}|null,
  reply:{request_id?:string;revision?:number;generation?:number;ticket?:{epoch:string}|null;type:string},
  socket:object|null,status:{revision:number;generation:number;ticket:{epoch:string}|null}|null):boolean {
  return !!pending && reply.request_id===pending.id && conditionReadIsCurrent(pending.socket,pending.status,socket,status)
    && (reply.type==="condition_diff" || (reply.type==="edited_condition" && reply.revision===pending.status.revision+1
      && reply.generation===pending.status.generation && reply.ticket?.epoch===pending.status.ticket?.epoch));
}

/** 毎回実sampleを取得する。初回欠測は入力待ち、取得後の喪失は明示stale。 */
export function createWorkbenchGamepadMessages(newSession:()=>string=()=>crypto.randomUUID()) {
  let epoch:string|null=null;
  let session="";
  let sequence=0;
  let observed=false;
  return (nextEpoch:string, pads:ArrayLike<ViewerGamepadLike|null|undefined>|null, timestamp:number)=>{
    if(epoch!==nextEpoch) {epoch=nextEpoch;session=newSession();sequence=0;observed=false;}
    const snapshot=sampleViewerGamepadSnapshot(pads);
    if(!snapshot.connected && !observed) return null;
    observed ||= snapshot.connected;
    return buildViewerGamepadControlMessage(snapshot,timestamp,
      {sequence:sequence++,metadata:{viewer_provider_session_id:session}});
  };
}

/** Workbenchもvisible寿命を共有し、送信時点のclaim/ticket/phaseを毎回検査する。 */
export function createWorkbenchGamepadLifecycle<T extends ViewerGamepadLike>(options: Omit<ViewerGamepadLifecycleOptions<T>, "publish" | "onSample"> & {
  context(): {epoch: string; enabled: boolean} | null;
  publish(message: NonNullable<ReturnType<ReturnType<typeof createWorkbenchGamepadMessages>>>, pads: ArrayLike<T|null|undefined>|null): void;
  nowSeconds(): number;
}) {
  const sample = createWorkbenchGamepadMessages();
  return createViewerGamepadLifecycle({...options, publish: undefined, neutralHeartbeat: false, pollIntervalMs: 1000 / 60,
    getGamepads: () => options.context()?.enabled ? options.getGamepads() : null, onSample(pads) {
    const context = options.context();
    if (!context?.enabled) return;
    const message = sample(context.epoch, pads, options.nowSeconds());
    if (message !== null) options.publish(message, pads);
  }});
}

/** runnerの停止理由を要求rejectionより優先する。次trialには旧理由を持ち越さない。 */
export function workbenchNotice(status: {phase:string; error:string|null;
  result?:{runner_stop_reason:string; error?:string|null}|null}|null, requestError:string):string {
  if (status?.error) return status.error;
  if (status && ["terminal","recording_failed"].includes(status.phase) && status.result) {
    return status.result.error || status.result.runner_stop_reason;
  }
  return requestError;
}
