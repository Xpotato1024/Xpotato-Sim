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
