import assert from "node:assert/strict";
import {renderToStaticMarkup} from "react-dom/server";
import {angleNeedle,angleSector,angleLabel,jointRailGroups,decodeJointDisplayLayout} from "../src/wasm-scene/jointPresentation.js";
import {assistPose,relativePane,scissorRect} from "../src/wasm-scene/viewportPresentation.js";
import {inputKind,inputAvailability,standardGamepad,browserGamepadDisplay} from "../src/ui/inputStripPresentation.js";
import {InputStrip} from "../src/ui/InputStrip.js";
import {JointInstruments} from "../src/ui/JointInstruments.js";
import {createInitialProductViewerState,buildProductViewerInputOverlayState} from "../src/wasm-scene/productViewerState.js";

for(const degrees of [0,-0,45,-45,90,-90,135,-135,180,-180,179,-179]) {
  const radians=degrees*Math.PI/180;
  const needle=angleNeedle(radians)!;
  assert.ok(Math.abs(needle.x-(42+25*Math.sin(radians)))<1e-10);
  assert.ok(Math.abs(needle.y-(43-25*Math.cos(radians)))<1e-10);
  if(degrees===0){assert.equal(angleSector(radians),null);assert.equal(angleLabel(degrees),'0°');}
  else assert.match(angleSector(radians)!,degrees>0?/A30 30 0 0 1/:/A30 30 0 0 0/);
}
for(const value of [null,NaN,Infinity,-Infinity,210*Math.PI/180,-210*Math.PI/180]) {
  assert.equal(angleNeedle(value),null);assert.equal(angleSector(value),null);
}
assert.equal(angleLabel(.1),'+<1°');assert.equal(angleLabel(-.9),'-<1°');assert.equal(angleLabel(210),'+210°');
const locals=['sholder_joint_1','sholder_joint_2','sholder_joint_3','elbow_joint'];
const names=['object',...locals.map(n=>'right__'+n),...locals.map(n=>'left__'+n)];
const full=decodeJointDisplayLayout(names,[0,3,3,3,3,3,3,3,3],[0,7,8,9,10,11,12,13,14],15);
const layout={...full,joints:full.joints.filter(j=>j.name!=='object')};
assert.deepEqual(jointRailGroups(layout,'fast_arm-assembly-mujoco-model/v3').map(g=>g.id),['left','right']);
assert.equal(jointRailGroups({...layout,joints:layout.joints.slice(0,4)},'fast_arm-assembly-mujoco-model/v3')[0].id,'right');
assert.equal(jointRailGroups(layout,'unknown')[0].label,'関節');
const state={...createInitialProductViewerState(),qposStatus:'ready' as const,jointLayout:layout,currentQpos:[0,0,0,1,0,0,0,...Array(8).fill(Math.PI/4)]};
assert.match(renderToStaticMarkup(<JointInstruments state={state}/>),/\+45°/);
assert.doesNotMatch(renderToStaticMarkup(<JointInstruments state={state} unavailable="別epoch"/>),/dial-needle/);
assert.match(renderToStaticMarkup(<JointInstruments state={state} terminal/>),/終了時/);
assert.deepEqual(relativePane({x:8,y:9,width:900,height:400},{x:96,y:31,width:640,height:378}),{x:88,y:22,width:640,height:378});
assert.equal(scissorRect({x:0,y:22,width:800,height:200},500).y,278);
const top=assistPose([1,2,3],1,'assist-top'),front=assistPose([1,2,3],1,'assist-front');
assert.deepEqual(top.up,[-1,0,0]);assert.deepEqual(front.up,[0,0,1]);assert.equal(top.position[1],front.position[1]);
const raw={mapping:'standard',id:'pad',index:0,sessionId:'session-a',sampledAtMs:10,axes:[.2,-.3,.4,.5],buttons:Array.from({length:17},()=>({pressed:false,value:0}))};
assert.ok(standardGamepad(raw));for(const other of [{...raw,mapping:''},{...raw,buttons:[]},null])assert.equal(standardGamepad(other),false);
assert.equal(browserGamepadDisplay([{...raw,connected:true,axes:[NaN]} as unknown as Gamepad],0),null);
const base={source_active:true,command_age_ms:1};
for(const [source,kind] of [['viewer_gamepad','gamepad'],['viewer_keyboard','keyboard'],['selfrionette','selfrionette'],['fixture','unknown']] as const){
  assert.equal(inputKind(source),kind);
  const input=buildProductViewerInputOverlayState({...base,source_kind:source})!;
  assert.equal(inputAvailability(input),null);
  assert.match(renderToStaticMarkup(<InputStrip state={{...state,inputOverlay:input}}/>),new RegExp(`data-source="${kind}"`));
  assert.ok(inputAvailability({...input,staleReason:'stale'}));
}
const input=buildProductViewerInputOverlayState({...base,source_kind:'viewer_gamepad',viewer_control_message:{metadata:{viewer_provider_session_id:'session-a'},gamepad:{id:'pad',index:0,connected:true,stale:false,axes:raw.axes,buttons:raw.buttons}}})!;
assert.match(renderToStaticMarkup(<InputStrip state={{...state,inputOverlay:input}} raw={raw}/>),/左stick XY/);
assert.doesNotMatch(renderToStaticMarkup(<InputStrip state={{...state,inputOverlay:input}} raw={{...raw,mapping:''}}/>),/左stick XY/);
assert.ok(inputAvailability(input,'keyboard'));assert.ok(inputAvailability({...input,gamepadConnected:false}));
const stale=renderToStaticMarkup(<InputStrip state={{...state,inputOverlay:{...input,staleReason:'disconnected'}}} raw={raw}/>);
assert.doesNotMatch(stale,/strip-stick/);assert.match(stale,/disconnected/);
assert.equal(inputAvailability({...input,commandAgeMs:5000}),null,'ageだけでsource契約を再判定しない');
assert.doesNotMatch(renderToStaticMarkup(<InputStrip state={{...state,inputOverlay:input}} raw={{...raw,sessionId:'old-session'}}/>),/左stick XY/);
const side={status:'armed' as const,zSign:1 as const,triggerValue:.8,triggerButton:6,signButton:4,velocity:[0,0,.1] as [number,number,number]};
const latched={...input,gamepadTriggerControl:{outputScope:'coordinated' as const,outputSide:null,endpointBindings:{left:'left',right:'right'},reason:null,sides:{left:side,right:{...side,triggerButton:7,signButton:5}}}};
const bumperRaw={...raw,buttons:raw.buttons.map((b,i)=>i===4?{pressed:true,value:1}:i===6?{pressed:true,value:.8}:b)};
const latchMarkup=renderToStaticMarkup(<InputStrip state={{...state,inputOverlay:latched}} raw={bumperRaw}/>);
assert.match(latchMarkup,/LB 押下/);assert.match(latchMarkup,/適用 \+Z/);assert.doesNotMatch(latchMarkup,/適用 -Z/);
assert.equal(standardGamepad({...raw,axes:[NaN,0,0,0]}),false);
assert.equal(standardGamepad({...raw,buttons:raw.buttons.map((b,i)=>i===0?{pressed:false,value:Infinity}:b)}),false);
console.log('operation projection: angles, address/epoch, named rails, cameras, sources, standard denial and stale PASS');

assert.equal(inputAvailability({...input,sourceKind:"n/a"},"gamepad"),"入力取得待ち");

// 表示だけの不正sampleがpollerへ例外を伝播させないことを確認する。
for (const malformed of [
  {...raw,connected:true,axes:undefined},
  {...raw,connected:true,buttons:undefined},
  {...raw,connected:true,buttons:[null]},
  {...raw,connected:true,mapping:null},
]) assert.equal(browserGamepadDisplay([malformed as unknown as Gamepad],10),null);
assert.equal(standardGamepad({...raw,buttons:[null,...raw.buttons.slice(1)]} as unknown as typeof raw),false);
assert.equal(browserGamepadDisplay([{...raw,connected:true} as unknown as Gamepad],NaN),null);
const throwingPad=Object.defineProperty({...raw,connected:true},'axes',{get(){throw new Error('display sample');}});
assert.equal(browserGamepadDisplay([throwingPad as unknown as Gamepad],10),null);
assert.equal(inputAvailability({...input,commandAgeMs:9999,staleReason:null}),null);
console.log('display-only malformed sample guard and backend-owned freshness PASS');
