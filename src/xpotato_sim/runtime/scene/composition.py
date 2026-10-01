"""box群とworldのMJCF合成。geometry/物体数ごとの実行loopは作らない。"""
import xml.etree.ElementTree as ET
from .contracts import ComposedObjectScene, ObjectSceneBuildRequest, ToolColliderBinding
from .objects import ObjectSceneManifest


def xml_values(values):
    """JSONで検証したSI値のlocale非依存表現。"""
    return " ".join(format(float(v), ".17g") for v in values)


def compose_object_scene(request: ObjectSceneBuildRequest) -> ComposedObjectScene:
    """Environmentは物体・worldと接触対を一度だけ追加する。base sceneは複製しない。"""
    if type(request) is not ObjectSceneBuildRequest or type(request.manifest) is not ObjectSceneManifest:
        raise TypeError("typed scene request required")
    if type(request.colliders) is not tuple or not request.colliders or any(type(c) is not ToolColliderBinding for c in request.colliders):
        raise ValueError("explicit Robot collider bindings required")
    if len({c.geom_name for c in request.colliders}) != len(request.colliders) or len({c.endpoint_id for c in request.colliders}) != len(request.colliders):
        raise ValueError("duplicate Robot collider bindings")
    tree = ET.fromstring(request.model_xml.decode("utf-8"))
    modern = request.manifest.world is not None
    if modern:
        from .world_composition import configure_world
        configure_world(tree, request.manifest, request.dynamics)
    if tree.tag != "mujoco" or tree.find("worldbody") is None:
        raise ValueError("MuJoCo worldbody required")
    trees=[tree]
    seen=set()
    for current in trees:
        for include in current.iter("include"):
            name=include.get("file")
            if name in seen or name not in request.model_assets or len(seen)>=32:
                raise ValueError("unresolved or repeated model include")
            seen.add(name)
            trees.append(ET.fromstring(request.model_assets[name]))
    names = {n.get("name") for subtree in trees for n in subtree.iter() if n.get("name")}
    geom_names = {n.get("name") for subtree in trees for n in subtree.iter("geom")}
    if any(c.geom_name not in geom_names for c in request.colliders):
        raise ValueError("Robot collider absent from model")
    world = tree.find("worldbody")
    pairs = tree.find("contact")
    if pairs is None:
        pairs = ET.SubElement(tree, "contact")
    bindings = []
    for obj in request.manifest.objects:
        spec = request.manifest.definition_for(obj)
        body_name, geom_name = "object__"+obj.instance_id, "object__"+obj.instance_id+"__geom"
        if body_name in names or geom_name in names:
            raise ValueError("object name collides with base model")
        names.update((body_name,geom_name))
        body = ET.SubElement(world, "body", {"name":body_name,"pos":xml_values(obj.position_m),"quat":xml_values(obj.orientation_wxyz)})
        if obj.motion_type=="dynamic":
            if not modern:
                raise ValueError("dynamic object requires explicit world")
            ET.SubElement(body,"freejoint",{"name":body_name+"__free","align":"false"})
        # fixedの質量を無限大にしない。worldへの固定はjointを持たないことで表す。
        ET.SubElement(body, "inertial", {"pos":"0 0 0","mass":str(spec.mass_kg),"diaginertia":xml_values(spec.diagonal_inertia_kg_m2)})
        ET.SubElement(body, "geom", {"name":geom_name,"type":"box","size":xml_values(spec.half_extents_m),
            "rgba":xml_values(spec.rgba),"friction":xml_values(spec.friction),"contype":"1" if modern else "0","conaffinity":"1" if modern else "0","group":"0",
            **({} if not modern else {"condim":str(request.manifest.contact.condim),
                "solref":xml_values(request.manifest.contact.solref),"solimp":xml_values(request.manifest.contact.solimp),
                "margin":str(request.manifest.contact.margin_m)})})
        bindings.append((obj.instance_id,geom_name))
        for collider in request.colliders:
            # 両geom同優先度のMuJoCo max規則をこの明示pairへ展開して保存する。
            friction = tuple(max(a,b) for a,b in zip(spec.friction,collider.friction))
            cp = request.manifest.contact
            ET.SubElement(pairs,"pair", {"name":f"pair__{collider.endpoint_id}__{obj.instance_id}",
                "geom1":collider.geom_name,"geom2":geom_name,"condim":str(cp.condim),
                "friction":xml_values((friction[0],friction[0],friction[1],friction[2],friction[2])),
                "solref":xml_values(cp.solref),"solimp":xml_values(cp.solimp),"margin":str(cp.margin_m),"gap":"0"})
    if modern:
        from .world_composition import finalize_home
        tree=finalize_home(tree,request.model_xml,request.model_assets,request.manifest)
    xml=ET.tostring(tree,encoding="utf-8")
    return ComposedObjectScene(xml,request.manifest,request.colliders,tuple(bindings),request.dynamics is not None)
