"""汎用OSCメッセージの検証とバイト列変換。"""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct


OscArgument = str | bytes | int | float
_OSC_DATAGRAM_MAX_BYTES = 65_507


def _valid_address(address: object) -> str:
    if not isinstance(address, str) or not address.startswith("/"):
        raise ValueError("OSC address must be an absolute path")
    if not address.isascii() or "\x00" in address:
        raise ValueError("OSC address must be non-empty ASCII without NUL")
    if address == "/" or address.endswith("/"):
        raise ValueError("OSC address must contain non-empty path segments")
    if any(
        not segment
        or any(not (character.isalnum() or character in "_.-") for character in segment)
        for segment in address[1:].split("/")
    ):
        raise ValueError("OSC address contains an unsupported path character")
    return address


@dataclass(frozen=True, slots=True)
class OscMessage:
    """OSC 1.0 addressとprimitive argumentsを持つimmutable message。"""

    address: str
    arguments: tuple[OscArgument, ...] = ()

    def __post_init__(self) -> None:
        _valid_address(self.address)
        if type(self.arguments) is not tuple:
            raise TypeError("OSC arguments must be an immutable tuple")
        for argument in self.arguments:
            if type(argument) is str:
                if "\x00" in argument:
                    raise ValueError("OSC string arguments must not contain NUL")
                argument.encode("utf-8", errors="strict")
            elif type(argument) is bytes:
                continue
            elif type(argument) is int:
                if not -(2**31) <= argument < 2**31:
                    raise ValueError("OSC integer arguments must fit signed int32")
            elif type(argument) is float:
                if not math.isfinite(argument):
                    raise ValueError("OSC float arguments must be finite")
            else:
                raise TypeError("OSC arguments must be str, bytes, int, or float")

    def to_bytes(self) -> bytes:
        return encode_osc_message(self)


def _encode_string(value: str) -> bytes:
    encoded = value.encode("utf-8", errors="strict") + b"\x00"
    return encoded + b"\x00" * ((-len(encoded)) % 4)


def _decode_string(document: bytes, offset: int) -> tuple[str, int]:
    terminator = document.find(b"\x00", offset)
    if terminator < 0:
        raise ValueError("OSC string is not NUL-terminated")
    padded_end = terminator + 1 + (-(terminator + 1 - offset)) % 4
    if padded_end > len(document) or any(document[terminator + 1 : padded_end]):
        raise ValueError("OSC string padding is invalid")
    try:
        value = document[offset:terminator].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("OSC string is not valid UTF-8") from exc
    return value, padded_end


def encode_osc_message(message: OscMessage) -> bytes:
    """Typed OSC 1.0 messageをcanonicalなdatagramへencodeする。"""

    if not isinstance(message, OscMessage):
        raise TypeError("expected OscMessage")
    chunks = [_encode_string(message.address)]
    type_tags = [","]
    argument_bytes: list[bytes] = []
    for argument in message.arguments:
        if type(argument) is str:
            type_tags.append("s")
            argument_bytes.append(_encode_string(argument))
        elif type(argument) is bytes:
            type_tags.append("b")
            padded_length = argument + b"\x00" * ((-len(argument)) % 4)
            argument_bytes.append(struct.pack(">i", len(argument)) + padded_length)
        elif type(argument) is int:
            type_tags.append("i")
            argument_bytes.append(struct.pack(">i", argument))
        elif type(argument) is float:
            type_tags.append("f")
            try:
                encoded_float = struct.pack(">f", argument)
            except (OverflowError, struct.error) as exc:
                raise ValueError("OSC float argument does not fit float32") from exc
            if not math.isfinite(struct.unpack(">f", encoded_float)[0]):
                raise ValueError("OSC float argument does not fit finite float32")
            argument_bytes.append(encoded_float)
        else:  # OscMessage validates this before encoding.
            raise TypeError("OSC arguments must be str, bytes, int, or float")
    chunks.append(_encode_string("".join(type_tags)))
    chunks.extend(argument_bytes)
    datagram = b"".join(chunks)
    if len(datagram) > _OSC_DATAGRAM_MAX_BYTES:
        raise ValueError("OSC datagram exceeds the UDP payload limit")
    return datagram


def decode_osc_message(document: bytes) -> OscMessage:
    """完全なOSC 1.0 message datagramを検証してdecodeする。"""

    if type(document) is not bytes:
        raise TypeError("OSC datagram must be bytes")
    if not document or len(document) > _OSC_DATAGRAM_MAX_BYTES:
        raise ValueError("OSC datagram size is invalid")
    address, offset = _decode_string(document, 0)
    _valid_address(address)
    type_tags, offset = _decode_string(document, offset)
    if not type_tags.startswith(","):
        raise ValueError("OSC type tag string must start with a comma")
    arguments: list[OscArgument] = []
    for type_tag in type_tags[1:]:
        if type_tag == "s":
            argument, offset = _decode_string(document, offset)
            arguments.append(argument)
        elif type_tag == "b":
            if offset + 4 > len(document):
                raise ValueError("OSC blob length is truncated")
            blob_length = struct.unpack_from(">i", document, offset)[0]
            offset += 4
            if blob_length < 0:
                raise ValueError("OSC blob length must be non-negative")
            blob_end = offset + blob_length
            padded_end = blob_end + (-blob_length) % 4
            if padded_end > len(document):
                raise ValueError("OSC blob is truncated")
            if any(document[blob_end:padded_end]):
                raise ValueError("OSC blob padding is invalid")
            arguments.append(document[offset:blob_end])
            offset = padded_end
        elif type_tag == "i":
            if offset + 4 > len(document):
                raise ValueError("OSC int32 argument is truncated")
            arguments.append(struct.unpack_from(">i", document, offset)[0])
            offset += 4
        elif type_tag == "f":
            if offset + 4 > len(document):
                raise ValueError("OSC float32 argument is truncated")
            value = struct.unpack_from(">f", document, offset)[0]
            if not math.isfinite(value):
                raise ValueError("OSC float32 argument must be finite")
            arguments.append(value)
            offset += 4
        else:
            raise ValueError(f"unsupported OSC type tag: {type_tag!r}")
    if offset != len(document):
        raise ValueError("OSC datagram has trailing bytes")
    return OscMessage(address=address, arguments=tuple(arguments))


__all__ = ["OscArgument", "OscMessage", "decode_osc_message", "encode_osc_message"]
