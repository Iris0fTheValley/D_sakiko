"""Bidirectional local bridge for the shared Live2D controller.

The bridge owns transport only.  A controller puts protocol dictionaries on
``event_queue`` and receives renderer facts from ``message_queue``.  No queue
is intercepted and no motion is inferred here.
"""

import asyncio
import mimetypes
import os
import posixpath
import threading
from queue import Queue
from typing import Optional
from urllib.parse import unquote

try:
    from .protocol import create_message
    from .ws_server import WSServer
except ImportError:  # direct execution compatibility
    from protocol import create_message
    from ws_server import WSServer

AUDIO_PORT = 9877


class Bridge:
    def __init__(self, event_queue: Queue, message_queue: Queue, audio_base: Optional[str] = None):
        self.event_queue = event_queue
        self.message_queue = message_queue
        self.audio_base = os.path.abspath(audio_base) if audio_base else None
        self.ws: Optional[WSServer] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._audio_server = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="live2d-bridge", daemon=True)
        self._thread.start()
        self._reader_thread = threading.Thread(target=self._read_events, name="live2d-bridge-events", daemon=True)
        self._reader_thread.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self.ws = WSServer(on_message=self._on_message)
        loop.run_until_complete(self.ws.start())
        if self.audio_base:
            loop.run_until_complete(self._start_audio_server())
        loop.run_forever()
        loop.run_until_complete(self.ws.stop())
        loop.close()

    async def _on_message(self, message: dict) -> None:
        self.message_queue.put(message)

    def _read_events(self) -> None:
        while not self._stopping.is_set():
            message = self.event_queue.get()
            if message is None:
                return
            if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                continue
            loop = self._loop
            if loop is None or self.ws is None:
                continue
            raw = create_message(
                "live2d_command",
                {"command": {"type": message["type"], "data": message.get("data", {})}},
                event_id=message.get("event_id"),
                session_id=message.get("session_id"),
                source=str(message.get("source") or "python"),
                seq=message.get("seq"),
            )
            asyncio.run_coroutine_threadsafe(self.ws.broadcast_raw(raw), loop)

    async def _start_audio_server(self) -> None:
        audio_base = self.audio_base

        async def handle_audio(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request = await asyncio.wait_for(reader.read(4096), timeout=5)
                first_line = request.decode("utf-8", errors="replace").split("\n", 1)[0]
                parts = first_line.split(" ")
                if len(parts) < 2 or parts[0] not in {"GET", "HEAD"}:
                    writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                    await writer.drain()
                    return
                path = unquote(parts[1].split("?", 1)[0])
                relative = path.removeprefix("/audio/").removeprefix("/model/")
                if path.startswith("/model/"):
                    relative = os.path.join("live2d_related", relative)
                candidate = os.path.abspath(os.path.join(audio_base, relative.replace("/", os.sep)))
                if os.path.commonpath([audio_base, candidate]) != audio_base or not os.path.isfile(candidate):
                    writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
                    await writer.drain()
                    return
                content_type = mimetypes.guess_type(candidate)[0] or "application/octet-stream"
                size = os.path.getsize(candidate)
                writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {size}\r\nAccess-Control-Allow-Origin: *\r\n\r\n".encode())
                if parts[0] == "GET":
                    with open(candidate, "rb") as file:
                        writer.write(file.read())
                await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        self._audio_server = await asyncio.start_server(handle_audio, "127.0.0.1", AUDIO_PORT)

    def shutdown(self) -> None:
        self._stopping.set()
        self.event_queue.put(None)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
