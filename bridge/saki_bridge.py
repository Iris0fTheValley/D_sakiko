"""
Saki Bridge — 纯 WebSocket 转发器（Electron 模式）

不再包含 queue_hook、motion_reader 等偷听逻辑。
只做一件事：从 main2.py 的 _bridge_queue 取事件 → WebSocket 广播给 Electron。

用法：
    from saki_bridge import Bridge
    bridge = Bridge(bridge_queue=_bridge_queue)
    bridge.start()
    # ... 程序退出时 ...
    bridge.shutdown()
"""
import asyncio
import threading
import sys
import os
from typing import Optional

# Python 3.9 兼容：全部用 Optional[X] 而非 X | None
bridge_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, bridge_dir)

from ws_server import WSServer

AUDIO_PORT = 9877  # HTTP 静态文件服务端口，供 Electron 加载音频


class Bridge:
    """简化的 Bridge 类，替代旧的 saki_launcher.py"""

    def __init__(self, bridge_queue, motion_queue=None, audio_base=None):
        self.bridge_q = bridge_queue
        self.motion_q = motion_queue
        self.audio_base = audio_base  # 音频文件根目录，用于 HTTP 静态服务
        self.ws = WSServer()
        self._reader_thread: Optional[threading.Thread] = None
        self._motion_reader_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self):
        """启动 WebSocket 服务 + 事件 reader 线程"""
        loop = asyncio.new_event_loop()
        self._loop = loop
        server_thread = threading.Thread(
            target=self._run_server_loop, args=(loop,), daemon=True
        )
        server_thread.start()

    def _run_server_loop(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.ws.start())
        # 启动音频 HTTP 静态文件服务
        if self.audio_base:
            try:
                loop.run_until_complete(self._start_audio_server())
            except OSError as e:
                print(f'[Audio HTTP] Failed to start on port {AUDIO_PORT}: {e}')
            except Exception as e:
                print(f'[Audio HTTP] Failed: {e}')
        # WS 服务启动后，开启 reader 线程
        self._reader_thread = threading.Thread(
            target=self._reader, daemon=True
        )
        self._reader_thread.start()
        # 开启 motion_event_queue reader（从 Pygame 进程读取动作事件）
        if self.motion_q is not None:
            self._motion_reader_thread = threading.Thread(
                target=self._motion_reader, daemon=True
            )
            self._motion_reader_thread.start()
        loop.run_forever()

    def _reader(self):
        """从 _bridge_queue 取事件 → WS 广播"""
        loop = self._loop
        while True:
            msg = self.bridge_q.get()
            if msg is None:  # shutdown() 发送的停止信号
                break
            if loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self.ws.broadcast(msg['type'], msg['data']), loop
                )

    def _motion_reader(self):
        """从 Pygame 进程的 motion_event_queue 读取动作事件 → WS 广播"""
        loop = self._loop
        while True:
            try:
                event = self.motion_q.get(timeout=5)
            except Exception:
                continue
            if event is None:
                break
            if loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self.ws.broadcast('motion', event), loop
                )

    async def _start_audio_server(self):
        """启动 HTTP 静态文件服务，供 Electron 加载音频文件"""
        audio_base = os.path.abspath(self.audio_base)
        print(f'[Audio HTTP] Base dir: {audio_base}', flush=True)
        print(f'[Audio HTTP] Starting on port {AUDIO_PORT}...', flush=True)
        async def handle_audio(reader, writer):
            try:
                request = await asyncio.wait_for(reader.read(4096), timeout=5)
                request_str = request.decode('utf-8', errors='replace')
                first_line = request_str.split('\n')[0] if request_str else ''
                parts = first_line.split(' ')
                if len(parts) < 2:
                    writer.close()
                    return
                url_path = parts[1].split('?')[0]
                # 处理 CORS 预检请求
                if parts[0] == 'OPTIONS':
                    writer.write(b'HTTP/1.0 204 No Content\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, OPTIONS\r\n\r\n')
                    writer.close()
                    return
                # URL: /audio/xxx.wav → 映射到 audio_base/xxx.wav
                rel = url_path.lstrip('/').replace('audio/', '', 1) if url_path.startswith('/audio/') else url_path.lstrip('/')
                filepath = os.path.normpath(os.path.join(audio_base, rel))
                # 安全检查：确保不越出 audio_base
                if not filepath.startswith(audio_base) or not os.path.isfile(filepath):
                    writer.write(b'HTTP/1.0 404 Not Found\r\n\r\n')
                    writer.close()
                    return
                with open(filepath, 'rb') as f:
                    data = f.read()
                response = b''.join([
                    b'HTTP/1.0 200 OK\r\n',
                    b'Content-Type: audio/wav\r\n',
                    b'Access-Control-Allow-Origin: *\r\n',
                    f'Content-Length: {len(data)}\r\n'.encode(),
                    b'\r\n',
                    data,
                ])
                writer.write(response)
                await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass
        await asyncio.start_server(handle_audio, '127.0.0.1', AUDIO_PORT)
        print(f'[Audio HTTP] Serving on http://127.0.0.1:{AUDIO_PORT}/audio/')

    def shutdown(self):
        """向 reader 线程发送停止信号"""
        if self.bridge_q is not None:
            self.bridge_q.put(None)
