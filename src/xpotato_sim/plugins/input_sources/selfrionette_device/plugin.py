"""品質付きDevice入力のdeclaration。import時に入力取得やprocessを開始しない。"""
from xpotato_sim.plugins.input_sources.registration import InputSourcePluginRegistration, InputSourcePluginRequest
from xpotato_sim.plugins.input_sources.selfrionette_device.source import DeviceSessionSource, build_reader, validate_parameters
from xpotato_sim.runtime.execution.input_source_adapters import RuntimeInputSourceExecutionAdapter, InputSourceExecutionSemantics
from xpotato_sim.runtime.experiment.contracts import ParameterContract, ParameterField, VersionedIdentity
from xpotato_sim.runtime.experiment.input_source import InputSourcePlugin, InputSourceMode, InputSourceHealth, InputSourceHealthStatus
from xpotato_sim.runtime.experiment.session_input import InputSourceSessionBinding


def _request(*, preset, source_parameters=None, **kwargs):
    if preset is not None or kwargs.get("frames") is not None or kwargs.get("line_source") is not None:
        raise ValueError("Device normalized source requires explicit source parameters")
    parameters = validate_parameters(source_parameters)
    return InputSourcePluginRequest(parameters, (), False, {"source_kind": "selfrionette_device"})


INPUT_SOURCE_PLUGIN = InputSourcePluginRegistration(
    plugin=InputSourcePlugin(
        identity=VersionedIdentity("selfrionette_device", 1),
        produced_sample_schema=VersionedIdentity("selfrionette_device_normalized_sample", 1),
        mode=InputSourceMode.LIVE, factory=build_reader,
        parameter_contract=ParameterContract((ParameterField("bridge_revision", str), ParameterField("bindings", object))),
        initial_health=InputSourceHealth(InputSourceHealthStatus.DISCONNECTED, reason="not_started", age_ms=0),
        initial_metadata={"source_kind": "selfrionette_device", "input_unit": "1"}),
    cli_aliases=("selfrionette_device",), request_builder=_request,
    execution_adapter=RuntimeInputSourceExecutionAdapter(VersionedIdentity("normalized_device_input_execution", 1),
        InputSourceExecutionSemantics.NORMALIZED_DEVICE_SOURCE),
    session_bindings=(InputSourceSessionBinding("selfrionette-device/v1", "selfrionette-device-input/v1", DeviceSessionSource,
        application_transport="selfrionette-bridge/v1"),))
