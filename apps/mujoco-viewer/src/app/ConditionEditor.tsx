import {useState} from "react";

export type Descriptor = {path:(string|number)[]; type:string; unit:string; minimum:number|null;
  maximum:number|null; exclusive_minimum:boolean; choices:(string|number)[]|null; available:boolean; reason:string|null;
  model_bindings?:Record<string,Record<string,string>>;model_configuration_digests?:Record<string,string>;
  motion_templates?:Record<string,Record<string,any>>};
export type Condition = {preset_id:string; [key:string]:any};
export function valueAt(document:any,path:(string|number)[]):any {return path.reduce((v,k)=>v?.[k],document);}
export function changedCondition(document:Condition,path:(string|number)[],value:any,descriptor?:Descriptor):Condition {
  const next=structuredClone(document); let target:any=next;
  for(const k of path.slice(0,-1)) target=target[k];
  target[path[path.length-1]]=value;
  if(path[path.length-1]==="motion_type") {
    const template=descriptor?.motion_templates?.[value];
    if(!template) throw new Error("backend motion templateがありません");
    delete target.initial_velocity;
    Object.assign(target,structuredClone(template));
  }
  return next;
}

/** 値域・可否・単位はbackendのdescriptorを表示する。物理状態は保持しない。 */
export function ConditionEditor({condition,descriptors,onChange,onValidate,onImport,onExport,onDiff,onError,disabled}:
  {condition:Condition|null;descriptors:Descriptor[];onChange:(c:Condition)=>void;onValidate:()=>void;
    onImport:(file:File)=>void;onExport:()=>void;onDiff:()=>void;onError:(text:string)=>void;disabled:boolean}) {
  const [advanced,setAdvanced]=useState(false);
  const [axis,setAxis]=useState(1);
  const groups=["Robot","Environment","Input","Mapping","Task","Evaluation","有限予算"];
  const group=(p:(string|number)[])=>p[0]==="limits"?6:p[0]==="environment"?1:p[0]==="task"?4:p[0]==="evaluation"?5:
    p[1]==="robot"||p[1]==="model"?0:p[1]==="input"?2:p[1]==="mapping"?3:6;
  const labelFor=(path:(string|number)[])=>{
    const labels:Record<string,string>={name:"登録名",version:"版",mass_kg:"質量",half_extents_m:"半寸法",position_m:"world位置",orientation_wxyz:"姿勢 wxyz",motion_type:"固定 / 可動",
      sliding_friction:"滑り摩擦",torsional_friction_m:"ねじり摩擦",rolling_friction_m:"転がり摩擦",rgba:"表示RGBA",gamepad_speed_m_s:"入力速度",gamepad_deadzone:"deadzone",gamepad_max_delta_m:"1入力の最大移動",
      max_ticks:"有限tick数",input_wait_s:"入力待機上限",wall_s:"実時間上限",prepare_s:"準備上限",dt_s:"制御周期",physics_dt_s:"物理周期",duration_s:"診断観測期間",iterations:"solver反復数",tolerance:"solver許容値",integrator:"積分器",provider:"入力provider",evaluation:"正式Evaluation"};
    let key=String(path[path.length-1]); const component=typeof path[path.length-1]==="number";
    if(component) key=String(path[path.length-2]);
    const kind=path.includes("definitions")?"物体定義":path.includes("objects")?"world配置":path.includes("world")?"world":path.includes("model")?"モデル":"";
    const index=component?` [${path[path.length-1]}]`:"";
    return `${kind} ${labels[key]??key}${index}`;
  };
  return <section className="condition-editor" aria-label="条件editor">
    <label><input type="checkbox" checked={advanced} onChange={e=>setAdvanced(e.target.checked)}/>Advanced設定</label>
    <p>次の編集条件は適用中の試行を変えません。検証・準備でnative modelを構築してからpreviewします。</p>
    {advanced && <nav aria-label="設定軸">{groups.map((name,index)=><button key={name} aria-pressed={axis===index} onClick={()=>setAxis(index)}>{name}</button>)}</nav>}
    {advanced && condition && groups.map((name,index)=><fieldset key={name} hidden={axis!==index} disabled={disabled}><legend>{name}</legend>
      <div className="condition-fields">{descriptors.filter(d=>group(d.path)===index && !["schema_version","preset_id"].includes(String(d.path[0]))
        && (d.available || ["name","provider","evaluation","environment","task","steps","interval_s","grace_period_s"].includes(String(d.path[d.path.length-1])))).map(d=>{
        const path=d.path.join("."); const value=valueAt(condition,d.path);
        return <label key={path} title={path}><span>{labelFor(d.path)} {d.unit && `(${d.unit})`}</span>
          {d.available?(d.choices?<select aria-label={path} value={value} onChange={e=>{
            const next=changedCondition(condition,d.path,typeof value==="number"?Number(e.target.value):e.target.value,d);
            if(d.model_bindings) next.configuration.coordination.side_to_endpoint=d.model_bindings[e.target.value];
            if(d.model_configuration_digests) next.model_configuration_sha256=d.model_configuration_digests[e.target.value];
            onChange(next);
          }}>
            {d.choices.map(v=><option key={v}>{v}</option>)}</select>:<input aria-label={path} type="number" value={value}
              min={d.minimum??undefined} max={d.maximum??undefined} step={d.type==="integer"?1:"any"}
              onChange={e=>onChange(changedCondition(condition,d.path,e.target.value===""?null:Number(e.target.value)))}/>):
            <><input aria-label={path} disabled value={value===null?"未対応 / 未選択":String(value)} readOnly/><small>{d.reason}</small></>}
        </label>;
      })}</div>
      {index===1 && <p>物体定義の半寸法・質量・表面と、同じworld内の配置を別項目として編集します。初期貫通はnative準備時に検査します。</p>}
      {index===2 && <p>named-model試行はviewer / gamepad/v1のみ対応。keyboard/offline/実機Sourceへの切替はこの経路では未対応です。</p>}
      {index===5 && <p>正式experimentのEvaluation・metricsは未対応です。接触観測Taskは診断であり、未評価を0や成功にしません。</p>}
      {index===0 && <p>model選択は表示された登録endpoint bindingも変更します。他のRobot固有寸法・gain編集はこのcatalogに公開されていません。</p>}
      {index===0 && <p>endpoint binding: {JSON.stringify(condition.configuration.coordination.side_to_endpoint)}</p>}
    </fieldset>)}
    <button disabled={disabled||!condition} onClick={onValidate}>次条件を検証</button>
    <button disabled={disabled||!condition} onClick={onExport}>条件をexport</button>
    <button disabled={disabled||!condition} onClick={onDiff}>適用条件との差分</button>
    <label>条件JSONをimport<input aria-label="条件JSONをimport" type="file" accept="application/json,.json" disabled={disabled}
      onChange={e=>{const file=e.target.files?.[0];if(file) {if(file.size>60000) onError("条件は60,000 bytes以内です");else onImport(file);}e.target.value="";}}/></label>
    <p>exportは展開済み条件/v1。importはbackendで再検証します。serverのfilesystem pathを指定する機能はありません。</p>
  </section>;
}
