"""
Proxy Queue — 偷听后端队列，不修改原数据流

使用方法：
    1. 获取 Saki 后端的原始 Queue 对象
    2. 用 ProxyQueue 包装它
    3. 替换原模块中的 Queue 引用
    4. 所有 put() 操作会被复制一份给 on_data 回调
"""
import queue
from typing import Any, Callable


class ProxyQueue:
    """
    偷听队列：代理一个 queue.Queue。
    数据写入时会：
        1. 照常写入原队列（不影响原有消费者）
        2. 同时调用 on_data 回调（给 Bridge 转发）
    """

    def __init__(self, original_queue: queue.Queue, on_data: Callable[[Any], None]):
        self._original = original_queue
        self._on_data = on_data

    def put(self, item: Any, *args, **kwargs) -> None:
        self._original.put(item, *args, **kwargs)
        try:
            self._on_data(item)
        except Exception as e:
            print(f"[ProxyQueue] on_data error: {e}")

    def get(self, *args, **kwargs) -> Any:
        return self._original.get(*args, **kwargs)

    def empty(self) -> bool:
        return self._original.empty()

    def qsize(self) -> int:
        return self._original.qsize()
