import {boundsCenter,perspectiveBoundsFit,orthographicHalfWidth} from "../src/wasm-scene/sceneFraming.js";
import assert from "node:assert/strict";
import {renderToStaticMarkup} from "react-dom/server";
import {angleNeedle,angleSector,angleLabel,jointRailGroups,decodeJointDisplayLayout} from "../src/wasm-scene/jointPresentation.js";
import {assistPose,relativePane,scissorRect} from "../src/wasm-scene/viewportPresentation.js";
import {inputKind,inputAvailability,standardGamepad,browserGamepadDisplay,signedTriggerInput} from "../src/ui/inputStripPresentation.js";
import {InputStrip,GamepadDiagnosticDetails} from "../src/ui/InputStrip.js";
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
assert.doesNotMatch(latchMarkup,/LB 押下|strip-buttons/);
assert.match(latchMarkup,/data-signed-value="0.8"/);
const latchDetails=renderToStaticMarkup(<GamepadDiagnosticDetails state={{...state,inputOverlay:latched}} raw={bumperRaw}/>);
assert.match(latchDetails,/LB 押下/);assert.match(latchDetails,/適用 \+Z/);assert.doesNotMatch(latchDetails,/適用 -Z/);
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

// 実sourceが使うinactiveと通信失効を区別する。中立表示は値の補完ではない。
const neutralGamepad={...input,sourceActive:false,staleReason:'gamepad_inactive',gamepadConnected:true,gamepadStale:false,gamepadZeroState:true};
assert.equal(inputAvailability(neutralGamepad),null);
assert.match(renderToStaticMarkup(<InputStrip state={{...state,inputOverlay:neutralGamepad}} raw={raw}/>),/左stick XY/);
for(const patch of [{gamepadConnected:false},{gamepadConnected:null},{gamepadStale:true},{gamepadStale:null},{gamepadZeroState:null},{gamepadZeroState:false},{staleReason:'command_age_ms_exceeded_timeout_250'}]) {
  assert.ok(inputAvailability({...neutralGamepad,...patch}));
}
const neutralKeyboard={...input,sourceKind:'viewer_keyboard',sourceActive:false,staleReason:'keyboard_inactive',keyboardFocusState:'focused',keyboardZeroState:true};
assert.equal(inputAvailability(neutralKeyboard),null);
assert.ok(inputAvailability({...neutralKeyboard,keyboardFocusState:'blurred'}));
assert.ok(inputAvailability({...neutralKeyboard,keyboardZeroState:null}));
assert.ok(inputAvailability({...neutralKeyboard,staleReason:'invalid_viewer_control_message'}));
console.log('neutral source samples remain visible; disconnected, blurred and timed-out samples stay invalid');

// v1.1: Zは同一backend sampleの確定符号付きtrigger量。rawでラッチを再解釈しない。
assert.equal(signedTriggerInput(latched,'left'),.8);
const negative={...latched,gamepadTriggerControl:{...latched.gamepadTriggerControl,sides:{...latched.gamepadTriggerControl.sides,left:{...side,zSign:-1 as const}}}};
assert.equal(signedTriggerInput(negative,'left'),-.8);
const negativeMarkup=renderToStaticMarkup(<InputStrip state={{...state,inputOverlay:negative}} raw={bumperRaw}/>);
assert.match(negativeMarkup,/data-signed-value="-0.8"/);
assert.doesNotMatch(negativeMarkup,/strip-buttons|LB 押下|Back|Start|B6 raw/);
for (const changed of [
  {...latched,staleReason:'timeout'},
  {...latched,gamepadTriggerControl:null},
  {...latched,motionStatus:'stopped'},
  {...latched,gamepadTriggerControl:{...latched.gamepadTriggerControl,reason:'invalid'}},
  {...latched,gamepadTriggerControl:{...latched.gamepadTriggerControl,endpointBindings:{right:'right'}}},
  {...latched,gamepadTriggerControl:{...latched.gamepadTriggerControl,sides:{...latched.gamepadTriggerControl.sides,left:{...side,status:'waiting_trigger_neutral' as const}}}},
]) assert.equal(signedTriggerInput(changed,'left'),null);
assert.equal(signedTriggerInput({...latched,gamepadTriggerControl:{...latched.gamepadTriggerControl,sides:{...latched.gamepadTriggerControl.sides,left:{...side,triggerValue:0}}}},'left'),0);
assert.equal(signedTriggerInput({...latched,gamepadTriggerControl:{...latched.gamepadTriggerControl,outputScope:'single_endpoint',outputSide:'right'}},'left'),null);
const noDirection=renderToStaticMarkup(<InputStrip state={{...state,inputOverlay:input}} raw={raw}/>);
assert.match(noDirection,/data-signed-value=""/);assert.doesNotMatch(noDirection,/data-signed-value="0"/);
console.log('signed vertical Z, diagnostic-only buttons and unknown values PASS');

// 表示geometryの画角検査。物理modelの可動域を仮定しない。
const bounds={min:[-1,-.5,-.1] as [number,number,number],max:[1,.5,.1] as [number,number,number]};
assert.deepEqual(boundsCenter(bounds),[0,0,0]);
const fit=perspectiveBoundsFit(bounds,[0,0,1],[0,1,0],2)!;
assert.ok(Math.abs(fit.distance-(.1+1.18*.5/Math.tan(Math.PI/8)))<1e-12);
assert.deepEqual(fit.position,[0,0,fit.distance]);
const narrowFit=perspectiveBoundsFit(bounds,[0,0,1],[0,1,0],.5)!;
assert.ok(narrowFit.distance>fit.distance);
assert.equal(perspectiveBoundsFit(bounds,[0,0,0],[0,1,0],1),null);
assert.equal(perspectiveBoundsFit(bounds,[0,0,1],[0,0,1],1),null);
assert.equal(perspectiveBoundsFit(bounds,[0,0,1],[0,1,0],0),null);
assert.equal(boundsCenter({min:[NaN,0,0],max:[1,1,1]}),null);
assert.equal(boundsCenter({min:[2,0,0],max:[1,1,1]}),null);
assert.equal(orthographicHalfWidth(bounds,1,1),1.18);
assert.equal(orthographicHalfWidth(bounds,2,1),2.36);
assert.equal(orthographicHalfWidth(bounds,0,1),null);
const shifted={min:[9,19.5,29.9] as [number,number,number],max:[11,20.5,30.1] as [number,number,number]};
assert.deepEqual(boundsCenter(shifted),[10,20,30]);
const reverse=perspectiveBoundsFit(shifted,[0,0,-1],[0,1,0],2)!;
assert.ok(reverse.position[2]<30);assert.ok(Math.abs(reverse.distance-fit.distance)<1e-12);
console.log('geometry-aware perspective/orthographic fit, aspect and direction preservation PASS');
