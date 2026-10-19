export type DevicePreparation = {ready:boolean;error:string|null;roles:Record<string,{
  phase:string;ready:boolean;pending:string|null;clock_synchronized:boolean;session_neutral_valid:boolean;error:string|null;
  expected:{source:{device_id:string;firmware_version:string;source_sha:string};profile:{record:string;method:string};operator:{operator:string;posture:string;grip:string}}}>};
const steps=[
  {action:"connect",phase:"configured",label:"個体を確認して接続"},
  {action:"tare",phase:"connected",label:"装置の無荷重を確認してtare"},
  {action:"neutral",phase:"awaiting_neutral",label:"使用姿勢で力を抜いてneutral取得"},
  {action:"apply",phase:"neutral_ready",label:"このprofileとneutralを適用"},
] as const;
const labels:Record<string,string>={unconfigured:"設定待ち",configured:"接続待ち",connected:"tare待ち",awaiting_neutral:"使用姿勢neutral待ち",
  capturing_neutral:"neutral取得中",neutral_ready:"適用待ち",applied:"入力の鮮度を確認中",streaming:"試行へ入力中",stopped:"入力停止・再準備が必要",failed:"入力障害・再準備が必要"};
/** 画面上の個別operator操作だけを送る。自動接続や一括ready操作を持たない。 */
export function DeviceInputPanel({value,enabled,onAction}:{value:DevicePreparation|null|undefined;enabled:boolean;
  onAction:(role:string,action:string,phase:string)=>void}) {
  if(!value)return null;
  return <section className="device-preparation" aria-label="Device入力の準備"><h2>Device入力の準備</h2>
    <p>個体・校正profile・使用条件を確認し、左右それぞれで順に操作してください。tareと使用姿勢のneutralは別の操作です。</p>
    {Object.entries(value.roles).map(([role,d])=><fieldset key={role}><legend>{role==="left"?"左":"右"} · {d.expected.source.device_id}</legend>
      <p>{labels[d.phase]??d.phase}{d.phase==="applied"&&d.ready?" · 開始準備完了":""}{d.pending?" · 処理中":""}</p>
      <p>profile {d.expected.profile.record} / method {d.expected.profile.method} / firmware {d.expected.source.firmware_version}</p>
      <p>operator {d.expected.operator.operator} / 姿勢 {d.expected.operator.posture} / 把持 {d.expected.operator.grip}</p>
      <div className="device-preparation__steps">{steps.map((step,i)=><button key={step.action}
        disabled={!enabled||!!d.pending||!d.clock_synchronized||d.phase!==step.phase}
        onClick={()=>onAction(role,step.action,d.phase)}>{i+1}. {step.label}</button>)}</div>
      {d.error&&<p role="alert">{d.error}</p>}
    </fieldset>)}
    {value.error&&<p role="alert">{value.error}</p>}
    <p>停止・再試行・再接続では準備をやり直します。開始後は、その試行で受け取った新しい中立入力を確認してから動作します。</p>
  </section>;
}
