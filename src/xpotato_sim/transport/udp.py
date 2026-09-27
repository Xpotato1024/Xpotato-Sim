"""構築時にI/Oを行わない、timeout付き単一datagram送信provider。"""

from __future__ import annotations

from collections.abc import Callable
import ipaddress
import socket
from dataclasses import dataclass
from typing import Literal, Protocol

from xpotato_sim.transport.endpoint import OscUdpEndpointConfig

DatagramEvidenceKind = Literal["simulated", "local_socket"]


class _DatagramSocket(Protocol):
    def settimeout(self, value: float) -> object: ...

    def sendto(self, data: bytes, address: tuple[object, ...]) -> int: ...

    def close(self) -> object: ...


@dataclass(frozen=True, slots=True)
class PreparedDatagramDestination:
    """DNS解決済みのdestination。socketはまだ開いていない。"""

    endpoint: OscUdpEndpointConfig
    address_family: int
    socket_address: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class DatagramSendReceipt:
    """senderが確認できたbyte countと、その証拠の強さ。"""

    datagram_bytes_accepted: int
    evidence_kind: DatagramEvidenceKind

    def __post_init__(self) -> None:
        if type(self.datagram_bytes_accepted) is not int or self.datagram_bytes_accepted < 0:
            raise ValueError("datagram byte count must be a non-negative integer")
        if self.evidence_kind not in {"simulated", "local_socket"}:
            raise ValueError("datagram send evidence kind is unknown")


class DatagramSender(Protocol):
    """runtime compositionが使う単一datagram送信interface。"""

    def prepare(self, endpoint: OscUdpEndpointConfig) -> PreparedDatagramDestination: ...

    @property
    def evidence_kind(self) -> DatagramEvidenceKind: ...

    def send(
        self, destination: PreparedDatagramDestination, datagram: bytes
    ) -> DatagramSendReceipt: ...


class UdpDatagramSender:
    """明示的な呼出しで一つのUDP datagramを送り、必ずlocal socketを閉じる。"""

    def __init__(
        self,
        *,
        socket_factory: Callable[[int, int], _DatagramSocket] | None = None,
    ) -> None:
        if socket_factory is not None and not callable(socket_factory):
            raise TypeError("socket_factory must be callable")
        self._socket_factory = socket_factory

    @property
    def evidence_kind(self) -> DatagramEvidenceKind:
        return "simulated" if self._socket_factory is not None else "local_socket"

    def prepare(self, endpoint: OscUdpEndpointConfig) -> PreparedDatagramDestination:
        if not isinstance(endpoint, OscUdpEndpointConfig):
            raise TypeError("UDP preflight requires OscUdpEndpointConfig")
        try:
            address = ipaddress.ip_address(endpoint.host)
            family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            socket_address: tuple[object, ...] = (
                (endpoint.host, endpoint.port, 0, 0)
                if family == socket.AF_INET6
                else (endpoint.host, endpoint.port)
            )
        except ValueError:
            candidates = socket.getaddrinfo(
                endpoint.host,
                endpoint.port,
                type=socket.SOCK_DGRAM,
                proto=socket.IPPROTO_UDP,
            )
            selected = next(
                (
                    (family, address)
                    for family, _type, _proto, _canonical_name, address in candidates
                    if family in {socket.AF_INET, socket.AF_INET6}
                ),
                None,
            )
            if selected is None:
                raise OSError("UDP endpoint did not resolve to an IP address")
            family, socket_address = selected
        return PreparedDatagramDestination(
            endpoint=endpoint,
            address_family=family,
            socket_address=socket_address,
        )

    def send(
        self, destination: PreparedDatagramDestination, datagram: bytes
    ) -> DatagramSendReceipt:
        if not isinstance(destination, PreparedDatagramDestination):
            raise TypeError("UDP send requires a preflighted datagram destination")
        if type(datagram) is not bytes:
            raise TypeError("UDP datagram must be bytes")
        if not datagram or len(datagram) > destination.endpoint.max_datagram_bytes:
            raise ValueError("UDP datagram size is outside endpoint limits")
        factory = self._socket_factory or socket.socket
        sock = factory(destination.address_family, socket.SOCK_DGRAM)
        try:
            sock.settimeout(destination.endpoint.timeout_s)
            accepted = sock.sendto(datagram, destination.socket_address)
            if type(accepted) is not int or accepted < 0:
                raise OSError("UDP provider returned an invalid accepted byte count")
            return DatagramSendReceipt(
                datagram_bytes_accepted=accepted,
                evidence_kind=self.evidence_kind,
            )
        finally:
            sock.close()


__all__ = [
    "DatagramEvidenceKind",
    "DatagramSendReceipt",
    "DatagramSender",
    "PreparedDatagramDestination",
    "UdpDatagramSender",
]
