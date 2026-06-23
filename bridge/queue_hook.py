"""
Queue 拦截器 — 零侵入运行时钩子

只在 import main2 之前替换 queue.Queue。
不动 multiprocessing.Queue（避免跨进程兼容性问题）。
"""
import queue

_queue_registry = []


class _TrackedQueue(queue.Queue):
    """带追踪的 queue.Queue"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _queue_registry.append(('queue.Queue', self))


def install():
    """安装 Queue 拦截器（仅 queue.Queue）"""
    queue.Queue = _TrackedQueue
    _queue_registry.clear()


def get_queues():
    """获取所有被拦截到的 Queue"""
    return list(_queue_registry)
