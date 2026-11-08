import { useEffect, useRef } from "react";

export function useQueueEvents(queueId, onChanged) {
  const callback = useRef(onChanged);
  useEffect(() => { callback.current = onChanged; }, [onChanged]);

  useEffect(() => {
    if (!queueId) return;
    let socket;
    let timer;
    let stopped = false;
    function connect() {
      const base = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
      const url = new URL(base, window.location.origin);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      url.pathname = "/ws";
      url.search = "";
      url.hash = "";
      socket = new WebSocket(url);
      socket.onopen = () => socket.send(JSON.stringify({ action: "subscribe", event_id: queueId }));
      socket.onmessage = (event) => {
        let data;
        try { data = JSON.parse(event.data); } catch { return; }
        if (data.type === "subscribed" ||
            (["queue.updated", "queue.deleted"].includes(data.type) && data.queue_id === queueId)) {
          callback.current(data);
        }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => { if (!stopped) timer = setTimeout(connect, 2000); };
    }
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ action: "unsubscribe", event_id: queueId }));
      }
      socket?.close();
    };
  }, [queueId]);
}
