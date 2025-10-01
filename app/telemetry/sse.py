import asyncio
import json
from typing import AsyncIterator, Dict, List

_subscribers: Dict[str, List[asyncio.Queue]] = {}

def _get_queue(run_id: str) -> asyncio.Queue:
    q = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append(q)
    return q

def publish_event(run_id: str, event: dict) -> None:
    queues = _subscribers.get(run_id, [])
    # Fan-out non-blocking
    for q in list(queues):
        try:
            q.put_nowait(event)
        except Exception:
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
        # remove q from subscribers
        lst = _subscribers.get(run_id, [])
        if q in lst:
            lst.remove(q)
        if not lst and run_id in _subscribers:
            _subscribers.pop(run_id, None)
