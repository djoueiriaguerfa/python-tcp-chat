"""Length-prefixed JSON protocol shared by the chat server and client."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any

HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 64 * 1024


class ProtocolError(Exception):
    """Raised when a peer sends an invalid protocol message."""


class MessageTooLargeError(ProtocolError):
    """Raised when a message exceeds the configured size limit."""


def receive_exactly(sock: socket.socket, number_of_bytes: int) -> bytes:
    """Receive exactly the requested bytes or raise ConnectionError on EOF."""
    if number_of_bytes < 0:
        raise ValueError("number_of_bytes cannot be negative")
    chunks: list[bytes] = []
    remaining = number_of_bytes
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("connection closed while receiving data")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_message(sock: socket.socket, message: dict[str, Any]) -> None:
    """Encode and send one length-prefixed JSON object."""
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    try:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("message is not JSON serializable") from exc
    if not payload:
        raise ProtocolError("message cannot be empty")
    if len(payload) > MAX_MESSAGE_SIZE:
        raise MessageTooLargeError(f"message exceeds the {MAX_MESSAGE_SIZE}-byte limit")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def receive_message(sock: socket.socket) -> dict[str, Any]:
    """Receive, decode, and validate one length-prefixed JSON object."""
    header = receive_exactly(sock, HEADER_SIZE)
    (payload_size,) = struct.unpack("!I", header)
    if payload_size == 0:
        raise ProtocolError("message payload cannot be empty")
    if payload_size > MAX_MESSAGE_SIZE:
        raise MessageTooLargeError(f"message exceeds the {MAX_MESSAGE_SIZE}-byte limit")
    payload = receive_exactly(sock, payload_size)
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("message payload is not valid UTF-8 JSON") from exc
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    message_type = message.get("type")
    if not isinstance(message_type, str) or not message_type:
        raise ProtocolError("message requires a non-empty string 'type'")
    return message
