/** backendの接触点・法線だけを描く。cube/colliderの複製やphysics計算を行わない。 */
import { ArrowHelper, Group, Mesh, MeshBasicMaterial, SphereGeometry, Vector3 } from "three";
import type { SceneContactPresentation } from "../contact/sceneContactPresentation.js";

export class SceneContactOverlay {
  readonly group = new Group();
  private geometry = new SphereGeometry(0.004, 10, 8);
  private materials = {
    near: new MeshBasicMaterial({transparent:true,depthTest:false,depthWrite:false,color:0x38bdf8}),
    touching: new MeshBasicMaterial({transparent:true,depthTest:false,depthWrite:false,color:0xfacc15}),
    penetrating: new MeshBasicMaterial({transparent:true,depthTest:false,depthWrite:false,color:0xf97316}),
  };
  private pool: { marker: Mesh; arrow: ArrowHelper }[] = [];
  private disposed = false;

  constructor() { this.group.name = "backend scene-contact geometry"; }

  private reserve(capacity: number): void {
    while (this.pool.length < capacity) {
      const marker = new Mesh(this.geometry, this.materials.near);
      const arrow = new ArrowHelper(new Vector3(1,0,0),new Vector3(),0.06,0xfacc15,0.015,0.008);
      // 食い込み中も確認できる診断overlay。形状そのものの透明度や衝突は変更しない。
      // transparent queueへ置き、透明cubeが後から法線を覆い隠さないようにする。
      for (const object of [marker,arrow]) object.traverse(item => {
        item.renderOrder = 1000;
        const material = (item as typeof item & {material?: {depthTest:boolean;depthWrite:boolean;transparent:boolean}}).material;
        if (material) {material.depthTest=false;material.depthWrite=false;material.transparent=true;}
      });
      this.group.add(marker,arrow);
      this.pool.push({marker,arrow});
    }
  }

  /** 観測を偽造せず、初回接触に必要なshaderだけを入力開始前に準備する。 */
  async prepare(compile: (objects: Group) => Promise<unknown>): Promise<void> {
    if (this.disposed) return;
    this.reserve(1);
    const {marker, arrow} = this.pool[0];
    // compileはvisible objectだけを対象にする。renderer開始前なので一時的に可視化しても画面には出ない。
    marker.visible = arrow.visible = true;
    try { await compile(this.group); }
    finally { marker.visible = arrow.visible = false; }
  }

  update(value: SceneContactPresentation): void {
    if (this.disposed) return;
    const contacts = value.status === "available" ? value.contacts : [];
    // validatorの上限と一致。高頻度frameごとにGPU資源を作り直さない。
    if (contacts.length > 256) throw new Error("scene contact overlay limit");
    this.reserve(contacts.length);
    for (let i=0;i<this.pool.length;i++) {
      const {marker,arrow} = this.pool[i], contact = contacts[i];
      marker.visible = arrow.visible = contact !== undefined;
      if (contact === undefined) continue;
      marker.name = `contact ${contact.endpointId}/${contact.objectId}`;
      marker.position.set(...contact.point);
      marker.material = this.materials[contact.relation];
      arrow.position.set(...contact.point);
      arrow.setDirection(new Vector3(...contact.normal));
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    const disposed = new Set<object>();
    this.group.traverse(object => {
      const render = object as typeof object & {
        geometry?: {dispose():void};
        material?: {dispose():void} | Array<{dispose():void}>;
      };
      const resources = [
        render.geometry,
        ...(Array.isArray(render.material) ? render.material : [render.material]),
      ];
      for (const resource of resources) {
        if (resource && !disposed.has(resource)) { resource.dispose(); disposed.add(resource); }
      }
    });
    if (!disposed.has(this.geometry)) this.geometry.dispose();
    for (const material of Object.values(this.materials)) if (!disposed.has(material)) material.dispose();
    this.group.clear(); this.pool = [];
  }
}
