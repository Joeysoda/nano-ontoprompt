import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";

type ResizeState = {
  startHeight: number;
  startY: number;
};

type UseResizableHeightOptions = {
  initialHeight?: number;
  minHeight?: number;
  maxHeight?: number;
  step?: number;
};

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

/**
 * Keeps a panel height locally adjustable without coupling the graph renderer
 * to layout concerns. Pointer dragging and keyboard resizing share the same
 * bounds, so the graph can safely observe the resulting container size.
 */
export function useResizableHeight({
  initialHeight = 590,
  minHeight = 360,
  maxHeight = 1000,
  step = 40,
}: UseResizableHeightOptions = {}) {
  const [height, setHeight] = useState(() =>
    clamp(initialHeight, minHeight, maxHeight),
  );
  const dragRef = useRef<ResizeState | null>(null);

  const resizeBy = useCallback(
    (delta: number) => {
      setHeight((current) => clamp(current + delta, minHeight, maxHeight));
    },
    [maxHeight, minHeight],
  );

  const beginResize = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.currentTarget.setPointerCapture?.(event.pointerId);
      dragRef.current = { startHeight: height, startY: event.clientY };
      document.body.style.cursor = "row-resize";
      document.body.style.userSelect = "none";
    },
    [height],
  );

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "ArrowUp") {
        event.preventDefault();
        resizeBy(-step);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        resizeBy(step);
      } else if (event.key === "Home") {
        event.preventDefault();
        setHeight(minHeight);
      } else if (event.key === "End") {
        event.preventDefault();
        setHeight(maxHeight);
      }
    },
    [maxHeight, minHeight, resizeBy, step],
  );

  useEffect(() => {
    const handlePointerMove = (event: globalThis.PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      setHeight(
        clamp(drag.startHeight + event.clientY - drag.startY, minHeight, maxHeight),
      );
    };
    const endResize = () => {
      dragRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", endResize);
    window.addEventListener("pointercancel", endResize);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", endResize);
      window.removeEventListener("pointercancel", endResize);
      endResize();
    };
  }, [maxHeight, minHeight]);

  return {
    height,
    beginResize,
    handleKeyDown,
    minHeight,
    maxHeight,
  };
}
