import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..usecase.websocket import websocketService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    connection_id = await websocketService.connect(ws)
    try:
        await _read_ws(ws, connection_id)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: connection_id=%s", connection_id)
    finally:
        websocketService.disconnect(connection_id)


async def _read_ws(ws: WebSocket, connection_id: uuid.UUID) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(
                code=message.get("code", 1000),
                reason=message.get("reason", ""),
            )

        text = message.get("text")
        if text is None:
            await ws.send_json({"type": "error", "message": "Expected text JSON object"})
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            await ws.send_json({"type": "error", "message": "Invalid JSON"})
            continue

        if not isinstance(data, dict):
            await ws.send_json({"type": "error", "message": "Expected an object"})
            continue

        action = data.get("action")
        if action in ("subscribe", "unsubscribe"):
            event_id = await _get_event_ws(ws, data)
            if event_id is None:
                continue
            if action == "subscribe":
                websocketService.subscribe(connection_id, event_id)
            else:
                websocketService.unsubscribe(connection_id, event_id)
        elif action == "ping":
            await ws.send_json({"type": "pong"})
        else:
            logger.warning("Unknown WS action: %s from conn=%s", action, connection_id)
            await ws.send_json({"type": "error", "message": "Unknown action"})


async def _get_event_ws(ws: WebSocket, data: dict) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(data.get("event_id")))
    except ValueError:
        await ws.send_json({"type": "error", "message": "Invalid event UUID"})
        return None
