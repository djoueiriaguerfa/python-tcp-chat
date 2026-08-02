import json
import socket
import struct
import unittest

from protocol import MAX_MESSAGE_SIZE, MessageTooLargeError, ProtocolError, receive_message, send_message


class PartialSocket:
    def __init__(self, data: bytes, chunk_size: int = 2) -> None:
        self.data = data
        self.chunk_size = chunk_size

    def recv(self, requested: int) -> bytes:
        amount = min(requested, self.chunk_size, len(self.data))
        chunk, self.data = self.data[:amount], self.data[amount:]
        return chunk


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.left, self.right = socket.socketpair()

    def tearDown(self) -> None:
        self.left.close()
        self.right.close()

    def test_sends_and_receives_framed_message(self) -> None:
        expected = {"type": "chat", "message": "hello 🌍"}
        send_message(self.left, expected)
        self.assertEqual(receive_message(self.right), expected)

    def test_partial_tcp_reads(self) -> None:
        message = {"type": "system", "message": "split into pieces"}
        payload = json.dumps(message).encode()
        stream = PartialSocket(struct.pack("!I", len(payload)) + payload)
        self.assertEqual(receive_message(stream), message)  # type: ignore[arg-type]

    def test_multiple_messages_in_one_stream(self) -> None:
        first = {"type": "chat", "message": "one"}
        second = {"type": "chat", "message": "two"}
        send_message(self.left, first)
        send_message(self.left, second)
        self.assertEqual(receive_message(self.right), first)
        self.assertEqual(receive_message(self.right), second)

    def test_rejects_invalid_json(self) -> None:
        payload = b"not-json"
        self.left.sendall(struct.pack("!I", len(payload)) + payload)
        with self.assertRaises(ProtocolError):
            receive_message(self.right)

    def test_rejects_oversized_incoming_message_before_payload(self) -> None:
        self.left.sendall(struct.pack("!I", MAX_MESSAGE_SIZE + 1))
        with self.assertRaises(MessageTooLargeError):
            receive_message(self.right)

    def test_rejects_oversized_outgoing_message(self) -> None:
        with self.assertRaises(MessageTooLargeError):
            send_message(self.left, {"type": "chat", "message": "x" * MAX_MESSAGE_SIZE})


if __name__ == "__main__":
    unittest.main()
