import { useLayoutEffect, useRef } from "react";

// Перестановка строк сохраняет зрительную связь со студентом; порядок задаёт API.
export function useRowMotion(signature) {
  const container = useRef(null);
  const previous = useRef(new Map());
  const animations = useRef([]);
  useLayoutEffect(() => {
    animations.current.forEach(animation => animation.cancel());
    animations.current = [];
    const positions = new Map();
    const reduced = typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const origin = container.current?.getBoundingClientRect().top || 0;
    const rows = Array.from(container.current?.querySelectorAll("[data-queue-row]") || []).map(row => ({
      row, key: row.dataset.queueRow, top: row.getBoundingClientRect().top - origin,
    }));
    rows.forEach(({ row, key, top }) => {
      const before = previous.current.get(key);
      if (!reduced && row.animate && before != null && Math.abs(before-top) > 1) {
        animations.current.push(row.animate([
          { transform: `translateY(${before-top}px)` }, { transform: "translateY(0)" },
        ], { duration: 220, easing: "cubic-bezier(.2,.7,.2,1)" }));
      }
      positions.set(key, top);
    });
    previous.current = positions;
    return () => animations.current.forEach(animation => animation.cancel());
  }, [signature]);
  return container;
}
