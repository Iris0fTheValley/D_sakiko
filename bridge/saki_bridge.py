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


class Bridge:
    """简化的 Bridge 类，替代旧的 saki_launcher.py"""

    def __init__(self, bridge_queue):
        self.bridge_q = bridge_queue
        self.ws = WSServer()
        self._reader_thread: Optional[threading.Thread] = None
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
        # WS 服务启动后，开启 reader 线程
        self._reader_thread = threading.Thread(
            target=self._reader, daemon=True
        )
        self._reader_thread.start()
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

    def shutdown(self):
        """向 reader 线程发送停止信号"""
        if self.bridge_q is not None:
            self.bridge_q.put(None)
