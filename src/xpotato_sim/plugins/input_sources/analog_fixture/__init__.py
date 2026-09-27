"""Analog fixture acquisition; mapping is selected from plugins.mappings."""

from collections.abc import Mapping

from xpotato_sim.plugins.input_sources.analog_fixture.source import (
    AnalogFixtureInputSource,
    AnalogFixtureSample,
    parse_analog_fixture_sample,
)


def build_reader(parameters: Mapping[str, object]) -> AnalogFixtureInputSource:
    samples = parameters.get("samples")
    if not isinstance(samples, tuple):
        raise ValueError("analog_fixture plugin requires tuple samples")
    return AnalogFixtureInputSource(samples)


__all__ = [
    "AnalogFixtureSample",
    "AnalogFixtureInputSource",
    "build_reader",
    "parse_analog_fixture_sample",
]
