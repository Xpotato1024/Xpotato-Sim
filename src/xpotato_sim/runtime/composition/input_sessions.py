"""登録済みInput Sourceのversionとproviderを、共通session契約へ解決する。"""
from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.runtime.experiment.contracts import PluginSelection
from xpotato_sim.runtime.experiment.session_input import InputSourceSessionBinding


def resolve_source_session(selection: PluginSelection, provider_id: str) -> InputSourceSessionBinding:
    """宣言を解決するだけで、入力取得・process・device接続は開始しない。"""
    registration = INPUT_SOURCE_CATALOG.resolve(selection.plugin_id)
    if selection.contract_version != registration.plugin.identity.version:
        raise ValueError("unsupported input source plugin version")
    return registration.resolve_session(provider_id)
