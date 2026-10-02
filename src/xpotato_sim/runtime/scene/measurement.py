"""構築済みsceneのnative幾何観測と初期重なり検証。力/Taskは所有しない。"""
from math import isfinite
import mujoco
import numpy as np
from xpotato_sim.mujoco_backend.contact_geometry import read_contact_geometry
from .contracts import ComposedObjectScene
from .objects import number, vector
from .observation import SceneContact, ObjectPose, SceneGeometryObservation


class SceneGeometryObserver:
    """Robot-owned colliderとinstance bindingをnative modelへ一度解決する。"""
    def __init__(self, model, scene: ComposedObjectScene, model_sha256: str):
        if type(scene) is not ComposedObjectScene:
            raise TypeError("composed scene required")
        self.model, self.scene, self.model_sha256 = model, scene, model_sha256
        self.scene_digest = scene.manifest.digest
        self.tools = {}
        self.objects = {}
        for collider in scene.colliders:
            gid=int(mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_GEOM,collider.geom_name))
            if gid < 0:
                raise ValueError("missing tool geom binding")
            self.tools[gid]=collider
        for object_id,name in scene.object_geoms:
            gid=int(mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_GEOM,name))
            if gid < 0:
                raise ValueError("missing object geom binding")
            self.objects[gid]=(object_id,name,int(model.geom_bodyid[gid]))
        if len(self.objects)!=len(scene.manifest.objects) or set(self.tools)&set(self.objects):
            raise ValueError("ambiguous scene role binding")

    def validate_initial(self, data):
        """固定物体間もnative distanceを使い、初期食い込みを見落とさない。"""
        ids=list(self.objects)
        candidates={(min(a,b),max(a,b)) for a in self.tools for b in ids}
        candidates.update((a,b) for i,a in enumerate(ids) for b in ids[i+1:])
        # Robotのbase sceneが供給するworld planeだけを支持面として調べる。
        planes=[i for i in range(self.model.ngeom) if int(self.model.geom_type[i])==int(mujoco.mjtGeom.mjGEOM_PLANE)
                and int(self.model.geom_bodyid[i])==0]
        candidates.update((a,b) for a in ids for b in planes)
        for a,b in sorted(candidates):
            distance=float(mujoco.mj_geomDistance(self.model,data,a,b,1.0,None))
            tolerance = self.scene.manifest.contact.initial_penetration_tolerance_m
            if not isfinite(distance) or distance < -tolerance:
                raise ValueError(f"initial scene penetration: {a}/{b}")
            # GJKが深く重なるbox対にも0を返す場合がある。0を非貫通の証明にしない。
            # 初期固定box同士は明確な隙間を要求する（床への支持接触はこの制約外）。
            if a in self.objects and b in self.objects and distance <= tolerance:
                raise ValueError(f"initial scene penetration or ambiguous fixed-object separation: {a}/{b}")

    def observe(self, data, *, frame_index: int) -> SceneGeometryObservation:
        """入力生成・physics step・force extractionなしで、現在frameだけを読む。"""
        if type(frame_index) is not int or frame_index < 0 or data.model is not self.model:
            raise ValueError("scene observation frame/model mismatch")
        time=number(float(data.time),nonnegative=True)
        if (data.warning.number > 0).any():
            raise ValueError("scene observation solver warning")
        poses=[]
        for gid,(oid,name,bid) in self.objects.items():
            pos=vector(tuple(float(v) for v in data.xpos[bid]),3)
            quat=vector(tuple(float(v) for v in data.xquat[bid]),4)
            if abs(sum(v*v for v in quat)-1)>1e-8:
                raise ValueError("invalid observed object orientation")
            poses.append(ObjectPose(oid,pos,quat))
        records=[]
        for index in range(data.ncon):
            c=data.contact[index]; a,b=int(c.geom1),int(c.geom2)
            if a in self.objects and b in self.tools:
                a,b=b,a; direction=-1.
            else:
                direction=1.
            if a not in self.tools or b not in self.objects:
                continue
            g=read_contact_geometry(c)
            frame=np.asarray(g.frame_world).reshape(3,3)
            if not np.allclose(frame@frame.T,np.eye(3),rtol=0,atol=1e-8):
                raise ValueError("invalid observed contact frame")
            tool=self.tools[a]; oid,name,_=self.objects[b]
            normal=tuple(direction*v for v in g.normal_world)
            relation="penetrating" if g.distance_m<0 else "touching" if g.distance_m==0 else "near"
            records.append(SceneContact(tool.endpoint_id,oid,tool.geom_name,name,g.point_world_m,normal,
                                        g.distance_m,g.penetration_m,relation))
        records.sort(key=lambda r:(r.endpoint_id,r.object_id,r.point_world_m,r.normal_world,r.distance_m))
        return SceneGeometryObservation(self.scene_digest,self.model_sha256,frame_index,time,
            tuple(sorted(poses,key=lambda p:p.instance_id)),tuple(records),self.scene.dynamic_execution)
