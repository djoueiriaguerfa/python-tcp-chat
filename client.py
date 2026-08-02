"""Interactive terminal client for the TCP chat server."""

from __future__ import annotations

import argparse
import socket
import threading
from typing import Any

from protocol import ProtocolError, receive_message, send_message
from server import DEFAULT_HOST, DEFAULT_PORT, HELP_TEXT


class ChatClient:
    """A client that receives in a background thread while the user types."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self.socket: socket.socket | None = None
        self._stopping = threading.Event()
        self._send_lock = threading.Lock()

    def connect(self) -> None:
        self.socket = socket.create_connection((self.host, self.port))
        print(f"[SYSTEM] Connected to {self.host}:{self.port}")

    def register(self) -> None:
        if self.socket is None:
            raise RuntimeError("client is not connected")
        while True:
            username = input("Username: ").strip()
            self._send({"type": "register", "username": username})
            response = receive_message(self.socket)
            if response.get("type") == "register" and response.get("status") == "ok":
                print(f"[SYSTEM] Registered as {response['username']}")
                return
            self._display(response)

    def run(self) -> None:
        if self.socket is None:
            raise RuntimeError("client is not connected")
        receiver = threading.Thread(target=self._receive_loop, name="chat-receiver")
        receiver.start()
        print(f"[SYSTEM] {HELP_TEXT}")
        try:
            while not self._stopping.is_set():
                try:
                    text = input()
                except EOFError:
                    text = "/quit"
                message = self.parse_input(text)
                if message is None:
                    continue
                self._send(message)
                if message["type"] == "disconnect":
                    self._stopping.set()
                    break
        except KeyboardInterrupt:
            print("\n[SYSTEM] Disconnecting...")
            try:
                self._send({"type": "disconnect"})
            except OSError:
                pass
            self._stopping.set()
        finally:
            self.close()
            receiver.join(timeout=2)

    @staticmethod
    def parse_input(text: str) -> dict[str, Any] | None:
        if not text.strip():
            print("[ERROR] Message cannot be empty.")
            return None
        if not text.startswith("/"):
            return {"type": "chat", "message": text}
        if text == "/users":
            return {"type": "user_list"}
        if text == "/help":
            return {"type": "help"}
        if text == "/quit":
            return {"type": "disconnect"}
        if text.startswith("/msg "):
            parts = text.split(maxsplit=2)
            if len(parts) == 3 and parts[1] and parts[2].strip():
                return {"type": "private", "to": parts[1], "message": parts[2]}
            print("[ERROR] Usage: /msg <username> <message>")
            return None
        print("[ERROR] Unknown command. Type /help for available commands.")
        return None

    def _send(self, message: dict[str, Any]) -> None:
        if self.socket is None:
            raise RuntimeError("client is not connected")
        with self._send_lock:
            send_message(self.socket, message)

    def _receive_loop(self) -> None:
        assert self.socket is not None
        try:
            while not self._stopping.is_set():
                message = receive_message(self.socket)
                self._display(message)
                if message.get("type") == "disconnect":
                    self._stopping.set()
                    break
        except (ConnectionError, OSError, ProtocolError) as exc:
            if not self._stopping.is_set():
                print(f"[ERROR] Connection lost: {exc}")
            self._stopping.set()

    @staticmethod
    def _display(message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "chat":
            print(f"[PUBLIC] {message.get('from')}: {message.get('message')}")
        elif message_type == "private":
            print(f"[PRIVATE] {message.get('from')} -> {message.get('to')}: {message.get('message')}")
        elif message_type == "user_list":
            print(f"[SYSTEM] Connected users: {', '.join(message.get('users', []))}")
        elif message_type == "error":
            print(f"[ERROR] {message.get('message', 'Unknown error')}")
        else:
            print(f"[SYSTEM] {message.get('message', '')}")

    def close(self) -> None:
        self._stopping.set()
        sock = self.socket
        self.socket = None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connect to the TCP chat server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="server address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="server port")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = ChatClient(args.host, args.port)
    try:
        client.connect()
        client.register()
        client.run()
    except (ConnectionError, OSError, ProtocolError) as exc:
        print(f"[ERROR] Unable to use chat server: {exc}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
