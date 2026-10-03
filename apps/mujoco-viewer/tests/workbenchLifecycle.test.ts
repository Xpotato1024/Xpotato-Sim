import assert from "node:assert/strict";
import {canConnect, createWorkbenchGamepadMessages, preparationIsCurrent} from "../src/app/workbenchLifecycle.js";
import {exportedHeapBytes} from "../src/wasm-scene/exportedHeap.js";
import {sampleViewerGamepadSnapshot, buildViewerGamepadControlMessage} from "../src/input/gamepadInput.js";

const a={readyState:0}, b={readyState:1};
assert.equal(canConnect(a),false);
assert.equal(canConnect(b),false);
assert.equal(canConnect({readyState:3}),true);
const load={socket:a,epoch:"a",generation:1};
const status={ticket:{epoch:"a"},generation:1,busy:null};
assert.equal(preparationIsCurrent(load,a,status),true);
assert.equal(preparationIsCurrent(load,b,status),false);
assert.equal(preparationIsCurrent(load,a,{...status,generation:2}),false);
assert.equal(preparationIsCurrent(load,a,{...status,ticket:{epoch:"b"}}),false);
assert.equal(preparationIsCurrent(load,a,{...status,busy:"stop"}),false);

let getterCalls=0;
const emscripten=Object.defineProperty({},"HEAPU8",{get(){getterCalls++;throw new Error("ABORT");}});
assert.equal(exportedHeapBytes(emscripten),null);
assert.equal(getterCalls,0);
assert.equal(exportedHeapBytes({}),null);
assert.equal(exportedHeapBytes({HEAPU8:new Uint8Array(128)}),128);

const sample=sampleViewerGamepadSnapshot([{connected:true,index:0,id:"test-pad",axes:[.05,-.6],buttons:[{pressed:true,value:.7}]}]);
const wire=buildViewerGamepadControlMessage(sample,12,{sequence:0,metadata:{viewer_provider_session_id:"trial-a"}});
assert.deepEqual(wire.gamepad?.raw_axes,[.05,-.6]);
assert.equal(wire.gamepad?.axes[0],0);
assert.equal(wire.gamepad?.buttons[0].value,.7);
assert.equal(wire.metadata?.viewer_provider_session_id,"trial-a");
const disconnected=buildViewerGamepadControlMessage(sampleViewerGamepadSnapshot(null),13);
assert.equal(disconnected.gamepad?.connected,false);
assert.equal(disconnected.gamepad?.stale,true);
assert.deepEqual(disconnected.gamepad?.axes,[]);
let sessions=0;
const acquire=createWorkbenchGamepadMessages(()=>`session-${++sessions}`);
const pads=[{connected:true,axes:[.05,-.6],buttons:[{pressed:true,value:.7}]}];
assert.equal(acquire("one",null,1),null,"初回欠測を中立sampleへ置き換えない");
const first=acquire("one",pads,2)!;
assert.deepEqual(first.gamepad?.raw_axes,[.05,-.6]);
assert.equal(first.sequence,0);
assert.equal(acquire("one",pads,3)?.sequence,1);
const lost=acquire("one",null,4)!;
assert.equal(lost.gamepad?.stale,true);
assert.equal(lost.gamepad?.connected,false);
assert.equal(lost.metadata?.viewer_provider_session_id,first.metadata?.viewer_provider_session_id);
assert.equal(acquire("two",null,5),null);
const second=acquire("two",pads,6)!;
assert.equal(second.sequence,0);
assert.notEqual(second.metadata?.viewer_provider_session_id,first.metadata?.viewer_provider_session_id);
console.log("Workbench socket/epoch, safe heap and raw input checks passed");

// 描画と独立した時計で実appの取得helperを測定する。
import {createWorkbenchGamepadLifecycle, workbenchNotice} from "../src/app/workbenchLifecycle.js";
const cadenceIntervals:number[][]=[];
for (const hz of [60, 30, 10]) {
  let now = 0, nextId = 1;
  const frame:{callback:(()=>void)|null}={callback:null};
  const timers = new Map<number, {at:number; callback:()=>void}>();
  const samples:number[]=[];
  const lifecycle=createWorkbenchGamepadLifecycle({
    window:{requestAnimationFrame(cb){frame.callback=cb;return 1;},cancelAnimationFrame(){frame.callback=null;},addEventListener(){},removeEventListener(){}},
    document:{visibilityState:"visible",addEventListener(){},removeEventListener(){}},
    context:()=>({epoch:"cadence",enabled:true}),getGamepads:()=>{samples.push(now);return pads;},
    nowMs:()=>now,nowSeconds:()=>now/1000,publish(){},
    setTimeoutFn:(callback,delay)=>{const id=nextId++;timers.set(id,{at:now+delay,callback});return id as unknown as ReturnType<typeof setTimeout>;},
    clearTimeoutFn:id=>{timers.delete(id as unknown as number);},
  });
  lifecycle.start();
  let nextFrame=1000/hz;
  while (Math.min(nextFrame,...[...timers.values()].map(t=>t.at))<=1000) {
    const timer=[...timers.entries()].sort((a,b)=>a[1].at-b[1].at)[0];
    if(timer&&timer[1].at<=nextFrame){now=timer[1].at;timers.delete(timer[0]);timer[1].callback();}
    else {now=nextFrame;nextFrame+=1000/hz;const cb=frame.callback;frame.callback=null;cb?.();}
  }
  const intervals=samples.slice(1).map((t,i)=>t-samples[i]);
  console.log(JSON.stringify({hz,samples:samples.length,min_ms:Math.min(...intervals),max_ms:Math.max(...intervals),mean_ms:intervals.reduce((a,b)=>a+b,0)/intervals.length}));
  lifecycle.dispose();
  assert.equal(timers.size,0);
  assert.equal(frame.callback,null);
  cadenceIntervals.push(intervals);
}
assert.ok(cadenceIntervals.every(intervals=>intervals.every(dt=>Math.abs(dt-1000/60)<1e-6)),"描画fpsと独立した約60Hz実取得");
const terminal={phase:"terminal",error:"original stale",result:{runner_stop_reason:"technical_invalid",error:"original stale"}};
assert.equal(workbenchNotice(terminal,"late rejection"),"original stale");
assert.equal(workbenchNotice({...terminal,error:null,result:{runner_stop_reason:"simulation_budget",error:null}},"late rejection"),"simulation_budget");
assert.equal(workbenchNotice({...terminal,error:null},"late rejection"),"original stale");
assert.equal(workbenchNotice({...terminal,phase:"running",error:null,result:null},"real input failure"),"real input failure");
assert.equal(workbenchNotice({...terminal,phase:"recording_failed",error:"disk failure"},"old rejection"),"disk failure");

import {telemetryIdentity,telemetryIsCurrent} from "../src/app/workbenchLifecycle.js";
const telemetryOld=telemetryIdentity({ticket:{epoch:"old"},generation:1,phase:"running"});
const telemetryNew=telemetryIdentity({ticket:{epoch:"new"},generation:1,phase:"ready"});
assert.equal(telemetryIsCurrent(telemetryOld,telemetryNew,telemetryNew,true),false);
assert.equal(telemetryIsCurrent(telemetryNew,telemetryNew,telemetryNew,true),true);
assert.equal(telemetryIsCurrent(telemetryNew,null,telemetryNew,true),false);
assert.equal(telemetryIsCurrent(telemetryNew,telemetryNew,telemetryNew,false),false);
assert.equal(telemetryIdentity({ticket:{epoch:"new"},generation:2,phase:"faulted"}),null);
assert.notEqual(telemetryIdentity({ticket:{epoch:"new"},generation:2,phase:"ready"}),telemetryNew);
const afterReconnect=telemetryIdentity({ticket:{epoch:"new"},generation:1,phase:"ready"},1);
assert.equal(telemetryIsCurrent(telemetryNew,afterReconnect,afterReconnect,true),false);
