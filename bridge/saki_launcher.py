"""
Saki Bridge 启动器 — 调试版（逐步排查 Pygame 卡死问题）

阶段1：只替换 Queue 构造器，不加 put 钩子
"""
import sys
import os
import asyncio
import threading
import runpy

bridge_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, bridge_dir)

saki_root = os.path.normpath(os.path.join(bridge_dir, '..', '..', 'DSakiko3.10'))
saki_gpt = os.path.join(saki_root, 'GPT_SoVITS')
sys.path.insert(0, saki_gpt)
os.chdir(saki_gpt)

import queue_hook
queue_hook.install()

from ws_server import WSServer

ws_server = WSServer()
loop_ref = None

import queue as _qmod
_bridge_queue = _qmod.Queue()


def _motion_reader(meq):
    """从 Live2D 子进程的 motion_event_queue 读取动作事件"""
    import sys as _s
    print('[MotionReader] Starting...', flush=True)
    while True:
        try:
            event = meq.get(timeout=5)
        except Exception:
            continue
        if event is None:
            break
        _s.stderr.write(f'[MotionReader] GOT: {event}\n')
        _s.stderr.flush()
        _bridge_queue.put(('motion', event))


def _bridge_worker():
    import sys as _s
    while True:
        msg = _bridge_queue.get()
        if msg is None: break
        loop = loop_ref
        _s.stderr.write(f'[_WORKER] type={msg[0]} loop={"OK" if loop else "NONE"}\n')
        _s.stderr.flush()
        if loop:
            asyncio.run_coroutine_threadsafe(
                ws_server.broadcast(msg[0], msg[1]), loop
            )


def hook_queues():
    """只留 dp2qt hook，禁用 mp.Queue hooks"""

    def log(msg):
        print(f"[Bridge] {msg}", flush=True)

    queues = queue_hook.get_queues()
    if not queues:
        log("No queues tracked yet, retry...")
        return False
    if not queues:
        log("No queues, retry...")
        return False

    import queue as qmod
    q_queues = [(i, q) for i, (t, q) in enumerate(queues) if t == 'queue.Queue' and isinstance(q, qmod.Queue)]
    mp_queues = [(i, q) for i, (t, q) in enumerate(queues) if t == 'mp.Queue']
    log(f"q.Queue:{len(q_queues)} mp.Queue:{len(mp_queues)}")

    # 不依赖索引——挂所有 queue.Queue，谁接到 assistant_segment_ready 谁就是 dp2qt
    hooked = 0
    for _, q in q_queues:
        _orig = q.put
        def make_new_put(orig):
            def _new_put(item, *a, **kw):
                orig(item, *a, **kw)
                if isinstance(item, dict) and item.get('type') == 'assistant_segment_ready':
                    txt = item.get('text', '')
                    if txt and txt.strip():
                        _bridge_queue.put(('text', txt))
                    # 动作和表情改由 live2d 的 motion_event_queue 精确提供
                    # 不再从 dp2qt 推导
            return _new_put
        q.put = make_new_put(_orig)
        hooked += 1
    log(f"Hooked all {hooked} queue.Queue instances (auto-detect dp2qt)")

    # 启动 motion_event_queue 读取线程
    import sys as _sys2
    _main = _sys2.modules.get('__main__')
    if _main and hasattr(_main, 'motion_event_queue'):
        meq = _main.motion_event_queue
        t = threading.Thread(target=_motion_reader, args=(meq,), daemon=True)
        t.start()
        log("Motion event reader started")
    return True


EMOTION_MAP = {
    'neutral': 'neutral', 'happy': 'happy', 'sad': 'sad',
    'angry': 'angry', 'surprise': 'surprise', 'fear': 'sad',
    'disgust': 'angry', 'bye': 'bye',
}

MOTION_GROUP_MAP = {
    'LABEL_0': 'happiness', 'LABEL_1': 'sadness', 'LABEL_2': 'anger',
    'LABEL_3': 'disgust', 'LABEL_4': 'like', 'LABEL_5': 'surprise', 'LABEL_6': 'fear',
}


def run_ws_server():
    global loop_ref
    loop = asyncio.new_event_loop()
    loop_ref = loop
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ws_server.start())

    for attempt in range(10):
        import time
        time.sleep(3)
        if hook_queues():
            break
        print(f"[Bridge] Retry {attempt + 1}/10...", flush=True)

    loop.run_forever()


if __name__ == '__main__':
    print("[Bridge] Installing queue hooks...")
    ws_thread = threading.Thread(target=run_ws_server, daemon=True)
    ws_thread.start()
    bridge_thread = threading.Thread(target=_bridge_worker, daemon=True)
    bridge_thread.start()

    print("[Bridge] Starting Saki main2...")
    main2_path = os.path.join(saki_gpt, 'main2.py')
    runpy.run_path(main2_path, run_name='__main__')
