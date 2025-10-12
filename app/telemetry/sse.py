import asyncio
import json
from typing import AsyncIterator, Dict, List, Tuple

# Store (queue, loop) to allow thread-safe publishing from worker threads
_subscribers: Dict[str, List[Tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = {}

def _get_queue(run_id: str) -> asyncio.Queue:
    q = asyncio.Queue()
    loop = asyncio.get_running_loop()
    _subscribers.setdefault(run_id, []).append((q, loop))
    return q

def publish_event(run_id: str, event: dict) -> None:
    items = _subscribers.get(run_id, [])
    # Fan-out in a thread-safe manner to the event loop owning each queue
    for (q, loop) in list(items):
        try:
            # Schedule put_nowait on the correct loop thread-safely
            loop.call_soon_threadsafe(q.put_nowait, event)
        except Exception:
            # Never crash publisher on telemetry issues
            pass

async def event_stream(run_id: str) -> AsyncIterator[bytes]:
    q = _get_queue(run_id)
    try:
        # Initial hello
        yield b"event: hello\n" + f"data: {json.dumps({'run_id': run_id})}\n\n".encode()
        while True:
            ev = await q.get()
            data = json.dumps(ev, ensure_ascii=False)
            yield b"event: update\n" + f"data: {data}\n\n".encode()
    finally:
        # remove (q, loop) from subscribers
        lst = _subscribers.get(run_id, [])
        # Find tuple with our q
        for item in list(lst):
            if item[0] is q:
                lst.remove(item)
        if not lst and run_id in _subscribers:
            _subscribers.pop(run_id, None)
