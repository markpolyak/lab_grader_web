import { useEffect, useState } from "react";

import { fetchCourseDetails } from "../../api";
import { courseHasQueue } from "./state";

/**
 * Есть ли у курса очередь: тогда на страницах групп и работ есть блок
 * QueueEntryCard. Ошибку запроса не показываем - блока просто нет.
 */
export function useCourseQueue(courseId) {
  const [hasQueue, setHasQueue] = useState(false);

  useEffect(() => {
    let current = true;
    setHasQueue(false);
    fetchCourseDetails(courseId)
      .then((details) => {
        if (current) setHasQueue(courseHasQueue(details));
      })
      .catch(() => {});
    return () => {
      current = false;
    };
  }, [courseId]);

  return hasQueue;
}
