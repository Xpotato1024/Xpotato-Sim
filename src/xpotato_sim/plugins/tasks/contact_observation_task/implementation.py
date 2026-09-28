"""接触達成を成功条件にせず、有限期間の幾何観測だけを行うTask。"""
from dataclasses import dataclass
from numbers import Real
from xpotato_sim.runtime.scene.task import GeometryTaskContext, GeometryTaskObservation
from xpotato_sim.runtime.scene.objects import identifier, number
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidence, CanonicalEvidenceSet, EvidenceStatus, ParameterContract, ParameterField,
    TaskPlugin, TaskTerminalClassification, TaskTransition, VersionedIdentity,
)

EVENT = VersionedIdentity("contact_observation_terminal",1)


@dataclass(frozen=True, slots=True)
class ObservationState:
    """サンプル列は蓄積せず、有界な観測対と最終frameだけを保持する。"""
    classification: TaskTerminalClassification = TaskTerminalClassification.RUNNING
    phase: str = "observing"
    frame_index: int = -1
    time_s: float = 0.
    observed_pairs: tuple[tuple[str,str], ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationBinding:
    """scene同一性と終了条件をチェックするpure Task実行。"""
    context: GeometryTaskContext
    targets: tuple[str, ...]
    duration_s: float

    def initial_state(self):
        return ObservationState()

    def advance(self, state, observation):
        if type(state) is not ObservationState or type(observation) is not GeometryTaskObservation:
            raise TypeError("typed geometry task state/observation required")
        if state.classification is not TaskTerminalClassification.RUNNING:
            raise ValueError("cannot advance terminal observation task")
        g=observation.geometry
        ids={o.instance_id for o in self.context.manifest.objects}
        valid=(g.scene_digest==self.context.manifest.digest and {o.instance_id for o in g.objects}==ids
            and g.frame_index>state.frame_index and g.simulation_time_s>=state.time_s
            and all(c.object_id in ids and c.endpoint_id in self.context.endpoint_ids for c in g.contacts))
        if not valid:
            cls,phase,reason=TaskTerminalClassification.TECHNICAL_INVALID,"invalid","geometry identity/time mismatch"
        elif observation.stopped_reason is not None:
            cls,phase,reason=TaskTerminalClassification.FAILURE,"aborted",observation.stopped_reason
        elif g.simulation_time_s >= self.duration_s:
            cls,phase,reason=TaskTerminalClassification.SUCCESS,"completed","observation window completed; not contact success"
        elif observation.budget_exhausted:
            cls,phase,reason=TaskTerminalClassification.FAILURE,"aborted","execution_budget_exhausted"
        else:
            cls,phase,reason=TaskTerminalClassification.RUNNING,"observing",None
        pairs=set(state.observed_pairs)
        if valid:
            pairs.update((c.endpoint_id,c.object_id) for c in g.contacts if c.object_id in self.targets and c.distance_m<=0.)
        next_state=ObservationState(cls,phase,g.frame_index,g.simulation_time_s,tuple(sorted(pairs)),reason)
        value={"schema_version":"contact-observation-presentation/v1","classification":cls.value,"phase":phase,"reason":reason,"epoch":self.context.epoch,"scene_digest":self.context.manifest.digest,
            "target_object_ids":self.targets,"observed_pairs":next_state.observed_pairs,"force_evaluated":False,
            "frame_index":g.frame_index,"simulation_time_s":g.simulation_time_s}
        evidence=CanonicalEvidence(EVENT,EvidenceStatus.MEASURED,value,"geometry_observation_task/v1")
        return TaskTransition(next_state,cls,CanonicalEvidenceSet((evidence,)))


class ObservationLifecycle:
    """対象選択は物体の配置/接触許可と独立にbindする。"""
    def initial_state(self, parameters):
        return ObservationState()

    def bind_context(self, context, parameters):
        if type(context) is not GeometryTaskContext:
            raise TypeError("GeometryTaskContext required")
        targets=parameters["target_object_ids"]
        if type(targets) is not tuple or not targets or len(targets)!=len(set(targets)):
            raise ValueError("distinct target object IDs required")
        if any(identifier(t) not in {o.instance_id for o in context.manifest.objects} for t in targets):
            raise ValueError("unknown task target object")
        return ObservationBinding(context,tuple(sorted(targets)),number(parameters["duration_s"],positive=True))


TASK_PLUGIN=TaskPlugin(
    identity=VersionedIdentity("contact_observation_task",1),lifecycle=ObservationLifecycle(),
    required_robot_capabilities=frozenset(),required_semantic_roles=frozenset(),
    parameter_contract=ParameterContract((ParameterField("target_object_ids",list,condition_specific=True),
        ParameterField("duration_s",Real,condition_specific=True))),
    task_event_identity=EVENT,produced_evidence=frozenset({EVENT}),compatible_backend_kinds=frozenset({"mujoco"}),
)
