import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";
import GlobalErrorBoundary from "@/components/GlobalErrorBoundary";

const CHUNK_RECOVERY_FLAG = "pulse_chunk_recovery_once";

const isChunkLoadFailure = (message = "") => {
  const text = String(message || "");
  return /Loading chunk .* failed|ChunkLoadError|Unexpected token '<'/i.test(text);
};

const clearLegacyWorkerAndCaches = async () => {
  const tasks = [];
  if ("serviceWorker" in navigator) {
    tasks.push(
      navigator.serviceWorker
        .getRegistrations()
        .then((regs) => Promise.all(regs.map((reg) => reg.unregister())))
        .catch(() => {}),
    );
  }
  if ("caches" in window) {
    tasks.push(
      window.caches
        .keys()
        .then((keys) => Promise.all(keys.map((key) => window.caches.delete(key))))
        .catch(() => {}),
    );
  }
  await Promise.all(tasks);
};

const reloadWithCacheBust = () => {
  const nextUrl = new URL(window.location.href);
  nextUrl.searchParams.set("__chunkfix", String(Date.now()));
  window.location.replace(nextUrl.toString());
};

const attemptChunkRecovery = async (rawMessage) => {
  if (!isChunkLoadFailure(rawMessage)) return;
  if (sessionStorage.getItem(CHUNK_RECOVERY_FLAG) === "1") return;
  sessionStorage.setItem(CHUNK_RECOVERY_FLAG, "1");
  await clearLegacyWorkerAndCaches();
  reloadWithCacheBust();
};

const installChunkRecoveryHandlers = () => {
  window.addEventListener("error", (event) => {
    const message = event?.message || event?.error?.message || "";
    void attemptChunkRecovery(message);
  });

  window.addEventListener("unhandledrejection", (event) => {
    const reason = event?.reason;
    const message =
      typeof reason === "string"
        ? reason
        : reason?.message || reason?.toString?.() || "";
    void attemptChunkRecovery(message);
  });
};

if (process.env.NODE_ENV === "development") {
  void clearLegacyWorkerAndCaches();
}
installChunkRecoveryHandlers();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <GlobalErrorBoundary>
      <App />
    </GlobalErrorBoundary>
  </React.StrictMode>,
);
