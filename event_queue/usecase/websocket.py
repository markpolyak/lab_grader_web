import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketService:
    def __init__(self) -> None:
        self.connect_ids: dict[uuid.UUID, WebSocket] = {}
        self.events: dict[uuid.UUID, set[uuid.UUID]] = {}

    async def connect(
        self,
        ws: WebSocket,
    ) -> uuid.UUID:
        await ws.accept()

        connect_id = uuid.uuid4()

        self.connect_ids[connect_id] = ws
        return connect_id

    def disconnect(
        self,
        connect_id: uuid.UUID,
    ) -> None:
        self.connect_ids.pop(connect_id, None)
        for event_id, ids in list(self.events.items()):
            ids.discard(connect_id)
            if not ids:
                self.events.pop(event_id, None)

    def subscribe(
        self,
        connect_id: uuid.UUID,
        event_id: uuid.UUID,
    ) -> None:
        if self.connect_ids.get(connect_id) is None:
            return
        ids = self.events.get(event_id)
        if ids is None:
            self.events[event_id] = {connect_id}
            return
        ids.add(connect_id)

    def unsubscribe(
        self,
        connect_id: uuid.UUID,
        event_id: uuid.UUID,
    ) -> None:
        if self.connect_ids.get(connect_id) is None:
            return
        ids = self.events.get(event_id)
        if ids is None:
            return
        ids.discard(connect_id)
        if not ids:
            self.events.pop(event_id)

    async def send_event(
        self,
        event_id: uuid.UUID,
        data: dict,
    ) -> None:
        ids = tuple(self.events.get(event_id, ()))
        for connect_id in ids:
            ws = self.connect_ids.get(connect_id)
            if ws is None:
                continue
            try:
                await ws.send_json(data)
            except (WebSocketDisconnect, OSError):
                logger.warning(
                    "Failed to send to connection_id=%s",
                    connect_id,
                    exc_info=True,
                )
                self.disconnect(connect_id)


websocketService = WebSocketService()
