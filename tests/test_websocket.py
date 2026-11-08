"""Exercise the application WebSocket route over real TCP connections."""
import asyncio
import json
import socket
import uuid
from datetime import date

import uvicorn
import httpx
import main
from websockets.asyncio.client import connect

from main import app
from queues.event.websocket import websocketService
from queues.usecase.queue_service import QueueService


def test_websocket_exchange(monkeypatch):
    asyncio.run(_exchange(monkeypatch))


async def _exchange(monkeypatch):
    monkeypatch.setattr(main.limiter, "enabled", False)
    service = websocketService
    event_id = uuid.uuid4()
    server = uvicorn.Server(
        uvicorn.Config(app, lifespan="off", log_level="warning")
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    if task.done():
                        await task
                        raise AssertionError("Server did not start")
                    await asyncio.sleep(0.01)
                url = f"ws://127.0.0.1:{listener.getsockname()[1]}/ws"
                async with connect(url) as first, connect(url) as second:
                    for invalid in ([], None, "text", 1, True):
                        await first.send(json.dumps(invalid))
                        assert json.loads(await first.recv())["message"] == "Expected an object"
                    await first.send(b"{}")
                    assert json.loads(await first.recv())["type"] == "error"
                    await first.send("{broken")
                    assert json.loads(await first.recv())["message"] == "Invalid JSON"
                    await first.send('{"action":"subscribe","event_id":"bad"}')
                    assert json.loads(await first.recv())["message"] == "Invalid event UUID"
                    for client in (first, second):
                        await client.send(json.dumps({"action": "subscribe", "event_id": str(event_id)}))
                        await _ping(client)
                    assert len(service.events[event_id]) == 2
                    payload = {"type": "update", "value": "Привет"}
                    await service.send_event(event_id, payload)
                    assert json.loads(await first.recv()) == payload
                    assert json.loads(await second.recv()) == payload
                    await first.send(json.dumps({"action": "unsubscribe", "event_id": str(event_id)}))
                    await _ping(first)
                    assert len(service.events[event_id]) == 1
                    await service.send_event(event_id, payload)
                    assert json.loads(await second.recv()) == payload
                    # No event should be queued on the unsubscribed connection.
                    await _ping(first)
                    # A successful queue mutation reaches every viewer by lesson ID.
                    queues = QueueService()
                    schedules = queues.generate_week(
                        {"os": {"name": "ОС", "university": "ГУАП"}},
                        [{"university": "suai", "days": {"2026-11-09": [{
                            "subject": "ОС", "kind": "Лабораторное занятие", "groups": ["4431"],
                            "starts-at": "10:00", "ends-at": "11:00",
                        }]}}], week_start=date(2026, 11, 9),
                    )
                    lesson_id = schedules["os"].lessons[0].id
                    for client in (first, second):
                        await client.send(json.dumps({"action": "subscribe", "event_id": str(lesson_id)}))
                        await _ping(client)
                    monkeypatch.setattr(main, "_get_current_schedule", lambda *args: (queues, schedules["os"]))
                    monkeypatch.setitem(app.dependency_overrides, main.require_admin, lambda: "test")
                    endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}/admin/courses/os/queues/{lesson_id}/registration"
                    async with httpx.AsyncClient() as client:
                        response = await client.put(endpoint, json={"closed": False})
                        assert response.status_code == 200, response.text
                    changed = {"type": "queue.updated", "queue_id": str(lesson_id)}
                    assert json.loads(await first.recv()) == changed
                    assert json.loads(await second.recv()) == changed
                    queues.set_registration(lesson_id, closed=False)  # no-op, no event
                    await _ping(first)
                    queues.begin_archive(schedules["os"].id, lesson_id)
                    queues.complete_archive(lesson_id)
                    # Background/service changes no longer emit socket messages.
                    await _ping(first)
                    await _ping(second)
                while event_id in service.events or service.connect_ids:
                    await asyncio.sleep(0.01)
                await service.send_event(event_id, payload)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)


async def _ping(client):
    await client.send('{"action":"ping"}')
    reply = json.loads(await client.recv())
    if reply.get("type") == "subscribed":
        reply = json.loads(await client.recv())
    assert reply == {"type": "pong"}
