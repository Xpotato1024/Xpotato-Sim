"""汎用UDP endpoint configuration。"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from typing import Mapping


OSC_UDP_ENDPOINT_SCHEMA_VERSION = "osc-udp-endpoint/v1"
_ENDPOINT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _host(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("host must be a non-empty canonical hostname or IP address")
    if any(character in value for character in "/\\@[]%"):
        raise ValueError("host must not contain URI, port, bracket, or zone syntax")
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass
    if ":" in value:
        raise ValueError("host must not contain a port or malformed IPv6 address")
    if len(value) > 253 or value != value.lower() or value.endswith("."):
        raise ValueError("DNS host must be lowercase without a trailing dot")
    labels = value.split(".")
    if not labels or any(not _DNS_LABEL.fullmatch(label) for label in labels):
        raise ValueError("host must be a valid ASCII DNS name or IP address")
    return value


@dataclass(frozen=True, slots=True)
class OscUdpEndpointConfig:
    """Robot固有の意味を持たないversion付きdatagram送信先。"""

    endpoint_id: str
    host: str
    port: int
    max_datagram_bytes: int = 1_200
    timeout_s: float = 0.25
    schema_version: str = OSC_UDP_ENDPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != OSC_UDP_ENDPOINT_SCHEMA_VERSION:
            raise ValueError(f"unsupported UDP endpoint schema_version: {self.schema_version!r}")
        if not isinstance(self.endpoint_id, str) or not _ENDPOINT_ID.fullmatch(self.endpoint_id):
            raise ValueError("endpoint_id must be a non-empty stable identifier")
        object.__setattr__(self, "host", _host(self.host))
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise ValueError("port must be an integer from 1 through 65535")
        if type(self.max_datagram_bytes) is not int or not 64 <= self.max_datagram_bytes <= 65_507:
            raise ValueError("max_datagram_bytes must be from 64 through 65507")
        if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, (int, float)):
            raise TypeError("timeout_s must be numeric")
        timeout_s = float(self.timeout_s)
        if timeout_s <= 0.0 or timeout_s != timeout_s or timeout_s in {float("inf"), float("-inf")}:
            raise ValueError("timeout_s must be finite and positive")
        object.__setattr__(self, "timeout_s", timeout_s)

    def to_json_value(self) -> dict[str, object]:
        return {
            "endpoint_id": self.endpoint_id,
            "host": self.host,
            "max_datagram_bytes": self.max_datagram_bytes,
            "port": self.port,
            "schema_version": self.schema_version,
            "timeout_s": self.timeout_s,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "OscUdpEndpointConfig":
        expected = {
            "endpoint_id",
            "host",
            "max_datagram_bytes",
            "port",
            "schema_version",
            "timeout_s",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("UDP endpoint fields are incomplete or unknown")
        if type(value["endpoint_id"]) is not str or type(value["host"]) is not str:
            raise ValueError("UDP endpoint identity and host must be strings")
        if type(value["schema_version"]) is not str:
            raise ValueError("UDP endpoint schema_version must be a string")
        if type(value["port"]) is not int or type(value["max_datagram_bytes"]) is not int:
            raise ValueError("UDP endpoint port and datagram limit must be integers")
        timeout_s = value["timeout_s"]
        if type(timeout_s) not in {int, float}:
            raise ValueError("UDP endpoint timeout_s must be numeric")
        return cls(
            endpoint_id=value["endpoint_id"],
            host=value["host"],
            port=value["port"],
            max_datagram_bytes=value["max_datagram_bytes"],
            timeout_s=timeout_s,
            schema_version=value["schema_version"],
        )


__all__ = ["OSC_UDP_ENDPOINT_SCHEMA_VERSION", "OscUdpEndpointConfig"]
