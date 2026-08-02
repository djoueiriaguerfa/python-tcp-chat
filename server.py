"""Concurrent terminal chat server."""

from __future__ import annotations

import argparse
import socket
import threading
from dataclasses import dataclass, field
from typing import Any

from protocol import ProtocolError, receive_message, send_message

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
USERNAME_MAX_LENGTH = 24
HELP_TEXT = "Available commands: /users, /msg <username> <message>, /help, /quit"


@dataclass(eq=False)
class ClientSession:
    """A connected client and the lock that serializes writes to it."""

    sock: socket.socket
    address: tuple[str, int]
    username: str | None = None
    send_lock: threading.Lock = field(default_factory=threading.Lock)

    def send(self, message: dict[str, Any]) -> None:
        with self.send_lock:
            send_message(self.sock, message)


class ChatServer:
    """Thread-per-client TCP chat server."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self._server_socket: socket.socket | None = None
        self._clients: dict[str, ClientSession] = {}
        self._clients_lock = threading.Lock()
        self._client_threads: set[threading.Thread] = set()
        self._threads_lock = threading.Lock()
        self._stopping = threading.Event()
        self.ready = threading.Event()

    @property
    def address(self) -> tuple[str, int]:
        if self._server_socket is None:
            return self.host, self.port
        address = self._server_socket.getsockname()
        return str(address[0]), int(address[1])

    def serve_forever(self) -> None:
        """Accept clients until shutdown is requested."""
        self._stopping.clear()
        self.ready.clear()
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket = server_socket
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server_socket.bind((self.host, self.port))
            server_socket.listen()
            server_socket.settimeout(0.5)
            self.ready.set()
            print(f"[SYSTEM] Server listening on {self.address[0]}:{self.address[1]}")
            while not self._stopping.is_set():
                try:
                    client_socket, address = server_socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        break
                    raise
                session = ClientSession(client_socket, (str(address[0]), int(address[1])))
                thread = threading.Thread(target=self._client_worker, args=(session,), name=f"chat-client-{address[1]}")
                with self._threads_lock:
                    self._client_threads.add(thread)
                thread.start()
        finally:
            self.shutdown()
            self._join_client_threads()
            self._server_socket = None
            self.ready.set()

    def shutdown(self) -> None:
        """Stop accepting connections and close every active client socket."""
        self._stopping.set()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
        with self._clients_lock:
            sessions = list(self._clients.values())
            self._clients.clear()
        for session in sessions:
            try:
                session.send({"type": "disconnect", "message": "Server is shutting down."})
            except (OSError, ProtocolError):
                pass
            self._close_socket(session.sock)

    def _join_client_threads(self) -> None:
        current = threading.current_thread()
        with self._threads_lock:
            threads = list(self._client_threads)
        for thread in threads:
            if thread is not current:
                thread.join(timeout=2)

    def _client_worker(self, session: ClientSession) -> None:
        try:
            if self._register(session):
                self._message_loop(session)
        except (ConnectionError, OSError):
            pass
        except ProtocolError as exc:
            self._safe_send(session, {"type": "error", "message": str(exc)})
        finally:
            self._remove_client(session)
            self._close_socket(session.sock)
            with self._threads_lock:
                self._client_threads.discard(threading.current_thread())

    def _register(self, session: ClientSession) -> bool:
        while not self._stopping.is_set():
            message = receive_message(session.sock)
            if message.get("type") == "disconnect":
                return False
            if message.get("type") != "register":
                self._safe_send(session, {"type": "error", "message": "Register a username first."})
                continue
            username = message.get("username")
            error = self._validate_username(username)
            if error:
                self._safe_send(session, {"type": "error", "message": error})
                continue
            assert isinstance(username, str)
            with self._clients_lock:
                duplicate = any(name.casefold() == username.casefold() for name in self._clients)
                if not duplicate:
                    session.username = username
                    self._clients[username] = session
            if duplicate:
                self._safe_send(session, {"type": "error", "message": "That username is already in use."})
                continue
            session.send({"type": "register", "status": "ok", "username": username})
            self._broadcast({"type": "system", "message": f"{username} joined the chat."}, exclude=session)
            print(f"[SYSTEM] {username} connected from {session.address[0]}:{session.address[1]}")
            return True
        return False

    @staticmethod
    def _validate_username(username: object) -> str | None:
        if not isinstance(username, str):
            return "Username must be text."
        if not username or username != username.strip():
            return "Username cannot be empty or start/end with spaces."
        if len(username) > USERNAME_MAX_LENGTH:
            return f"Username cannot exceed {USERNAME_MAX_LENGTH} characters."
        if not all(character.isalnum() or character in "_-" for character in username):
            return "Username may contain only letters, numbers, '_' and '-'."
        return None

    def _message_loop(self, session: ClientSession) -> None:
        while not self._stopping.is_set():
            message = receive_message(session.sock)
            message_type = message.get("type")
            if message_type == "chat":
                self._handle_chat(session, message.get("message"))
            elif message_type == "private":
                self._handle_private(session, message.get("to"), message.get("message"))
            elif message_type == "user_list":
                with self._clients_lock:
                    users = sorted(self._clients, key=str.casefold)
                self._safe_send(session, {"type": "user_list", "users": users})
            elif message_type == "help":
                self._safe_send(session, {"type": "system", "message": HELP_TEXT})
            elif message_type == "disconnect":
                return
            else:
                self._safe_send(session, {"type": "error", "message": f"Unknown message type: {message_type!r}"})

    def _handle_chat(self, session: ClientSession, content: object) -> None:
        error = self._validate_content(content)
        if error:
            self._safe_send(session, {"type": "error", "message": error})
            return
        assert isinstance(content, str) and session.username is not None
        self._broadcast({"type": "chat", "from": session.username, "message": content})
        print(f"[PUBLIC] {session.username}: {content}")

    def _handle_private(self, session: ClientSession, recipient_name: object, content: object) -> None:
        if not isinstance(recipient_name, str) or not recipient_name:
            self._safe_send(session, {"type": "error", "message": "Private message needs a recipient."})
            return
        error = self._validate_content(content)
        if error:
            self._safe_send(session, {"type": "error", "message": error})
            return
        with self._clients_lock:
            recipient = next((client for name, client in self._clients.items() if name.casefold() == recipient_name.casefold()), None)
        if recipient is None:
            self._safe_send(session, {"type": "error", "message": f"User '{recipient_name}' is not connected."})
            return
        assert isinstance(content, str) and session.username is not None
        private_message = {"type": "private", "from": session.username, "to": recipient.username, "message": content}
        self._safe_send(recipient, private_message)
        if recipient is not session:
            self._safe_send(session, private_message)
        print(f"[PRIVATE] {session.username} -> {recipient.username}: {content}")

    @staticmethod
    def _validate_content(content: object) -> str | None:
        if not isinstance(content, str):
            return "Message content must be text."
        if not content.strip():
            return "Message cannot be empty."
        return None

    def _broadcast(self, message: dict[str, Any], exclude: ClientSession | None = None) -> None:
        with self._clients_lock:
            recipients = [client for client in self._clients.values() if client is not exclude]
        for recipient in recipients:
            self._safe_send(recipient, message)

    @staticmethod
    def _safe_send(session: ClientSession, message: dict[str, Any]) -> bool:
        try:
            session.send(message)
            return True
        except (ConnectionError, OSError, ProtocolError):
            return False

    def _remove_client(self, session: ClientSession) -> None:
        username = session.username
        if username is None:
            return
        with self._clients_lock:
            removed = self._clients.get(username) is session
            if removed:
                del self._clients[username]
        if removed and not self._stopping.is_set():
            self._broadcast({"type": "system", "message": f"{username} left the chat."})
            print(f"[SYSTEM] {username} disconnected")

    @staticmethod
    def _close_socket(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the TCP chat server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="address to bind")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to bind")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ChatServer(args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SYSTEM] Shutting down server...")
        server.shutdown()
    except OSError as exc:
        print(f"[ERROR] Server error: {exc}")


if __name__ == "__main__":
    main()
