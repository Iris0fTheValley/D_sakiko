"""Small dependency-free WebSocket server used by the local bridge."""

import asyncio
import base64
import hashlib
import json
import struct
from typing import Awaitable, Callable, Optional, Set

try:
    from .protocol import parse_message
except ImportError:  # direct execution compatibility
    from protocol import parse_message

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MessageHandler = Callable[[dict], Awaitable[None]]


class WSServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 9876, on_message: Optional[MessageHandler] = None):
        self.host = host
        self.port = port
        self.on_message = on_message
        self._clients: Set[asyncio.StreamWriter] = set()
        self._server: Optional[asyncio.AbstractServer] = None

    async def _read_http_request(self, reader: asyncio.StreamReader) -> str:
        data = b""
        while b"\r\n\r\n" not in data and b"\n\n" not in data:
            chunk = await reader.read(1024)
            if not chunk:
                break
            data += chunk
        return data.decode("utf-8", errors="replace")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(self._read_http_request(reader), timeout=5)
            key = next((line.split(":", 1)[1].strip() for line in request.replace("\r\n", "\n").split("\n") if line.lower().startswith("sec-websocket-key:")), None)
            if not key:
                writer.close()
                return
            accept = base64.b64encode(hashlib.sha1(key.encode() + GUID.encode()).digest()).decode()
            writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            await writer.drain()
            self._clients.add(writer)
            while True:
                frame = await self._read_frame(reader)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:
                    await self._send_frame(writer, 0x8, b"")
                    break
                if opcode == 0x9:
                    await self._send_frame(writer, 0xA, payload)
                elif opcode == 0x1 and self.on_message is not None:
                    try:
                        message = parse_message(payload.decode("utf-8"))
                        if message is not None:
                            await self.on_message(message)
                    except Exception:
                        pass
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_frame(self, reader: asyncio.StreamReader):
        try:
            header = await asyncio.wait_for(reader.readexactly(2), timeout=60)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await reader.readexactly(8))[0]
        mask = await reader.readexactly(4) if masked else b""
        payload = bytearray(await reader.readexactly(length))
        if masked:
            for index in range(length):
                payload[index] ^= mask[index % 4]
        return opcode, bytes(payload)

    async def _send_frame(self, writer: asyncio.StreamWriter, opcode: int, payload: bytes) -> None:
        frame = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.extend([126, (length >> 8) & 0xFF, length & 0xFF])
        else:
            frame.append(127)
            frame.extend(struct.pack(">Q", length))
        frame.extend(payload)
        writer.write(bytes(frame))
        await writer.drain()

    async def broadcast_raw(self, message: str) -> None:
        payload = message.encode("utf-8")
        dead = set()
        for writer in tuple(self._clients):
            try:
                await self._send_frame(writer, 0x1, payload)
            except Exception:
                dead.add(writer)
        self._clients.difference_update(dead)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for writer in tuple(self._clients):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()
