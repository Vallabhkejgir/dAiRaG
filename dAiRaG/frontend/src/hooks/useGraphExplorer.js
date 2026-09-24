import { useEffect } from "react";
import { mountGraphExplorer } from "../graph/explorerEngine.js";

export function useGraphExplorer() {
  useEffect(() => {
    let cleanup;
    let cancelled = false;

    mountGraphExplorer().then((teardown) => {
      if (cancelled) {
        if (typeof teardown === "function") {
          teardown();
        }
        return;
      }
      cleanup = teardown;
    });

    return () => {
      cancelled = true;
      if (typeof cleanup === "function") {
        cleanup();
      }
    };
  }, []);
}
