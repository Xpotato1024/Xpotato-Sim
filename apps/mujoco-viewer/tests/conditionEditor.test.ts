import assert from "node:assert/strict";
import {changedCondition,valueAt} from "../src/app/ConditionEditor.js";
import {conditionReadIsCurrent} from "../src/app/workbenchLifecycle.js";

const applied={preset_id:"test",environment:{objects:[{motion_type:"fixed",pose:{position_m:[0,0,1]}}]}};
const next=changedCondition(applied,["environment","objects",0,"pose","position_m",2],.6);
assert.equal(valueAt(next,["environment","objects",0,"pose","position_m",2]),.6);
assert.equal(applied.environment.objects[0].pose.position_m[2],1);
const dynamic=changedCondition(next,["environment","objects",0,"motion_type"],"dynamic");
assert.deepEqual(dynamic.environment.objects[0].initial_velocity.linear_m_s,[0,0,0]);
assert.equal("initial_velocity" in changedCondition(dynamic,["environment","objects",0,"motion_type"],"fixed").environment.objects[0],false);
const socket={},captured={revision:2,generation:1,ticket:{epoch:"old"}};
assert.equal(conditionReadIsCurrent(socket,captured,socket,{...captured}),true);
assert.equal(conditionReadIsCurrent(socket,captured,{},captured),false);
assert.equal(conditionReadIsCurrent(socket,captured,socket,{...captured,revision:3}),false);
assert.equal(conditionReadIsCurrent(socket,captured,socket,{...captured,generation:2}),false);
assert.equal(conditionReadIsCurrent(socket,captured,socket,{...captured,ticket:{epoch:"new"}}),false);
assert.equal(conditionReadIsCurrent(socket,{...captured,ticket:null},socket,{...captured,ticket:null}),true);
console.log("condition editor: 10 assertions passed");
