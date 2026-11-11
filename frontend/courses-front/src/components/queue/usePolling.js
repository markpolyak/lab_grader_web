import { useEffect, useRef, useState } from "react";

import { createPoller } from "./state";


/**
 * Опрос страницы очереди (createPoller в state.js). На скрытой вкладке стоит
 * и возобновляется, когда её снова открыли; после размонтирования запросов нет.
 * Возвращает «обновить сейчас» - его вызывают после своего действия.
 */
export function usePolling(poll) {
  const pollRef = useRef(poll);
  const [poller] = useState(() =>
    createPoller(() => pollRef.current(), { isHidden: () => document.hidden })
  );

  useEffect(() => {
    pollRef.current = poll;
  }, [poll]);

  useEffect(() => {
    poller.start();
    const onVisibilityChange = () => {
      if (document.hidden) poller.pause();
      else poller.run();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      poller.stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [poller]);

  return poller.run;
}
