/** 同じsceneの全paneへ固定scaleの矢印を描く。GPU資源はpoolに保持する。 */
import {ArrowHelper,Group,Vector3} from "three";
import type {ForcePresentation} from "./forcePresentation.js";

export class ForceOverlay {
  readonly group=new Group();
  private pool:ArrowHelper[]=[];
  private direction=new Vector3();
  private disposed=false;
  private reserve(capacity:number):void {
    if(capacity>1024)throw new Error("bounded force overlay required");
    while(this.pool.length<capacity){
      const arrow=new ArrowHelper(new Vector3(1,0,0),new Vector3(),.05,0xe11d48,.012,.007);
      for(const part of [arrow.line,arrow.cone]) {
        part.renderOrder=1000;
        for(const material of (Array.isArray(part.material)?part.material:[part.material])) {
          material.depthTest=false;material.depthWrite=false;material.transparent=true;
        }
      }
      this.pool.push(arrow);this.group.add(arrow);
    }
  }
  /** 両比較条件で同じshaderを入力開始前に準備する。 */
  async prepare(compile:(objects:Group)=>Promise<unknown>):Promise<void> {
    if(this.disposed)return;
    this.reserve(1);this.pool[0].visible=true;
    try {await compile(this.group);} finally {this.pool[0].visible=false;}
  }
  update(value:ForcePresentation):void {
    if(this.disposed)return;
    const pairs=value.status==="available"?value.pairs.filter(p=>p.status==="active"&&p.force&&p.point&&p.length>0):[];
    this.reserve(pairs.length);
    this.pool.forEach((arrow,i)=>{
      const p=pairs[i];arrow.visible=!!p;if(!p)return;
      arrow.position.set(...p.point as [number,number,number]);
      const force=p.force!,magnitude=Math.hypot(...force);
      this.direction.set(force[0]/magnitude,force[1]/magnitude,force[2]/magnitude);
      arrow.setDirection(this.direction);arrow.setLength(p.length,Math.min(.015,p.length*.3),Math.min(.008,p.length*.2));
      arrow.setColor(p.saturated?0xf59e0b:0xe11d48);arrow.name=`object_on_tool ${p.endpoint}/${p.object}`;
    });
  }
  dispose():void {
    if(this.disposed)return;this.disposed=true;
    const disposed=new Set<object>();
    for(const arrow of this.pool)for(const part of [arrow.line,arrow.cone]){
      for(const resource of [part.geometry,...(Array.isArray(part.material)?part.material:[part.material])])
        if(!disposed.has(resource)){resource.dispose();disposed.add(resource);}
    }
    this.pool=[];this.group.clear();
  }
}
