import socket
import threading
import time
import unittest

from protocol import receive_message, send_message
from server import ChatServer


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ChatServer("127.0.0.1", 0)
        self.server_thread = threading.Thread(target=self.server.serve_forever, name="test-server")
        self.server_thread.start()
        self.assertTrue(self.server.ready.wait(2), "server did not start")
        self.clients: list[socket.socket] = []

    def tearDown(self) -> None:
        for client in self.clients:
            try:
                client.close()
            except OSError:
                pass
        self.server.shutdown()
        self.server_thread.join(3)
        self.assertFalse(self.server_thread.is_alive(), "server thread did not stop")

    def connect(self) -> socket.socket:
        client = socket.create_connection(self.server.address, timeout=2)
        client.settimeout(2)
        self.clients.append(client)
        return client

    def register(self, username: str) -> socket.socket:
        client = self.connect()
        send_message(client, {"type": "register", "username": username})
        response = receive_message(client)
        self.assertEqual(response["type"], "register")
        self.assertEqual(response["status"], "ok")
        return client

    @staticmethod
    def receive_until(client: socket.socket, message_type: str) -> dict:
        for _ in range(10):
            message = receive_message(client)
            if message.get("type") == message_type:
                return message
        raise AssertionError(f"did not receive {message_type!r}")

    def test_username_registration(self) -> None:
        client = self.register("Alice")
        send_message(client, {"type": "user_list"})
        self.assertEqual(receive_message(client)["users"], ["Alice"])

    def test_duplicate_username_is_rejected_case_insensitively(self) -> None:
        self.register("Alice")
        duplicate = self.connect()
        send_message(duplicate, {"type": "register", "username": "alice"})
        response = receive_message(duplicate)
        self.assertEqual(response["type"], "error")
        self.assertIn("already", response["message"])

    def test_public_message_is_broadcast_to_all_clients(self) -> None:
        alice = self.register("Alice")
        bob = self.register("Bob")
        self.receive_until(alice, "system")
        send_message(alice, {"type": "chat", "message": "Hello everyone"})
        for client in (alice, bob):
            message = self.receive_until(client, "chat")
            self.assertEqual(message["from"], "Alice")
            self.assertEqual(message["message"], "Hello everyone")

    def test_private_message_goes_to_sender_and_recipient(self) -> None:
        alice = self.register("Alice")
        bob = self.register("Bob")
        self.receive_until(alice, "system")
        send_message(alice, {"type": "private", "to": "bob", "message": "secret"})
        for client in (alice, bob):
            message = self.receive_until(client, "private")
            self.assertEqual(message["from"], "Alice")
            self.assertEqual(message["to"], "Bob")
            self.assertEqual(message["message"], "secret")

    def test_users_lists_connected_users(self) -> None:
        alice = self.register("Alice")
        self.register("Charlie")
        self.register("Bob")
        self.receive_until(alice, "system")
        self.receive_until(alice, "system")
        send_message(alice, {"type": "user_list"})
        self.assertEqual(receive_message(alice)["users"], ["Alice", "Bob", "Charlie"])

    def test_clean_disconnection_notifies_remaining_clients(self) -> None:
        alice = self.register("Alice")
        bob = self.register("Bob")
        self.receive_until(alice, "system")
        send_message(bob, {"type": "disconnect"})
        notice = self.receive_until(alice, "system")
        self.assertIn("Bob left", notice["message"])

    def test_unexpected_disconnection_notifies_remaining_clients(self) -> None:
        alice = self.register("Alice")
        bob = self.register("Bob")
        self.receive_until(alice, "system")
        bob.shutdown(socket.SHUT_RDWR)
        bob.close()
        notice = self.receive_until(alice, "system")
        self.assertIn("Bob left", notice["message"])

    def test_empty_and_invalid_messages_return_errors(self) -> None:
        alice = self.register("Alice")
        send_message(alice, {"type": "chat", "message": "   "})
        self.assertEqual(receive_message(alice)["type"], "error")
        send_message(alice, {"type": "mystery"})
        self.assertEqual(receive_message(alice)["type"], "error")

    def test_help(self) -> None:
        alice = self.register("Alice")
        send_message(alice, {"type": "help"})
        response = receive_message(alice)
        self.assertEqual(response["type"], "system")
        self.assertIn("/msg", response["message"])

    def test_three_concurrent_clients_can_broadcast(self) -> None:
        clients = [self.register(name) for name in ("Alice", "Bob", "Charlie")]
        # Drain join notices before starting simultaneous sends.
        for count, client in enumerate(clients):
            for _ in range(2 - count):
                self.receive_until(client, "system")

        barriers = threading.Barrier(4)

        def send_chat(client: socket.socket, text: str) -> None:
            barriers.wait()
            send_message(client, {"type": "chat", "message": text})

        senders = [
            threading.Thread(target=send_chat, args=(client, f"message-{index}"))
            for index, client in enumerate(clients)
        ]
        for sender in senders:
            sender.start()
        barriers.wait()
        for sender in senders:
            sender.join(2)
            self.assertFalse(sender.is_alive())

        for client in clients:
            received = {self.receive_until(client, "chat")["message"] for _ in range(3)}
            self.assertEqual(received, {"message-0", "message-1", "message-2"})


if __name__ == "__main__":
    unittest.main()
