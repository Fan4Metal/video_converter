"""
Единственный экземпляр приложения.

Проводник запускает отдельный процесс на каждый выделенный файл. Первый
процесс открывает окно и слушает локальный сокет, остальные пересылают ему
свои пути и завершаются. Номер порта хранится в файле во временной папке.
"""
import os
import socket
import tempfile
import threading
import time
from collections.abc import Callable
INSTANCE_NAME = "video_converter_single_instance"
INSTANCE_PORT_FILE = os.path.join(tempfile.gettempdir(), "video_converter.port")
INSTANCE_ACK = b"VC_OK"


def send_paths_to_running_instance(paths: list[str], timeout: float = 15.0) -> bool:
    """
    Пересылает пути уже запущенному окну. Пустой список просто поднимает окно.
    Повторяет попытки, пока первый экземпляр запускается и ещё не открыл сокет.
    """
    deadline = time.monotonic() + timeout
    payload = ("\n".join(paths) + "\n").encode("utf-8")
    while time.monotonic() < deadline:
        try:
            with open(INSTANCE_PORT_FILE, encoding="utf-8") as f:
                port = int(f.read().strip())
            with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
                conn.sendall(payload)
                conn.shutdown(socket.SHUT_WR)
                if conn.recv(len(INSTANCE_ACK)) == INSTANCE_ACK:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    return False


class InstanceServer:
    """Локальный сокет, через который другие копии приложения передают пути."""

    def __init__(self, on_paths: Callable[[list[str]], None]):
        self._on_paths = on_paths
        self._server: socket.socket | None = None

    def start(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen()
        with open(INSTANCE_PORT_FILE, "w", encoding="utf-8") as f:
            f.write(str(server.getsockname()[1]))
        self._server = server
        threading.Thread(target=self._loop, args=(server,), daemon=True).start()

    def close(self):
        if self._server is None:
            return
        self._server.close()
        self._server = None
        try:
            os.remove(INSTANCE_PORT_FILE)
        except OSError:
            pass

    def _loop(self, server: socket.socket):
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return  # сокет закрыт при выходе
            with conn:
                try:
                    conn.settimeout(5)
                    chunks = []
                    while chunk := conn.recv(65536):
                        chunks.append(chunk)
                    paths = [p for p in b"".join(chunks).decode("utf-8").splitlines() if p]
                    conn.sendall(INSTANCE_ACK)
                except OSError:
                    continue
            self._on_paths(paths)
