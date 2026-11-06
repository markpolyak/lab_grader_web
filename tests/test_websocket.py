"""Exercise the application WebSocket route over real TCP connections."""
import asyncio
import json
import socket
import uuid

import uvicorn
from websockets.asyncio.client import connect

from main import app
from queue.event.websocket import websocketService


def test_websocket_exchange():
    asyncio.run(_exchange())


async def _exchange():
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
                while event_id in service.events or service.connect_ids:
                    await asyncio.sleep(0.01)
                await service.send_event(event_id, payload)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)


async def _ping(client):
    await client.send('{"action":"ping"}')
    assert json.loads(await client.recv()) == {"type": "pong"}
