"""固定長vectorの静的契約。pytestではなくmypyの明示対象。"""
from typing import assert_type

from xpotato_sim.runtime.contact.manifest import _vector as manifest_vector
from xpotato_sim.runtime.contact.log import _vector as log_vector, _optional_vector

assert_type(manifest_vector("position", (1., 2., 3.), length=3), tuple[float, float, float])
assert_type(manifest_vector("orientation", (1., 0., 0., 0.), length=4), tuple[float, float, float, float])
assert_type(log_vector("force", (1., 2., 3.), length=3), tuple[float, float, float])
assert_type(_optional_vector("force", None, length=3), tuple[float, float, float] | None)
assert_type(_optional_vector("orientation", None, length=4), tuple[float, float, float, float] | None)
