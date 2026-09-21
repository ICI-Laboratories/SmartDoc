import { csrf } from "./api";

type EventName =
  | "library_view"
  | "page_view"
  | "citation_open"
  | "theme_changed"
  | "search_mode_changed"
  | "answer_feedback";
type EventValue =
  "light" | "dark" | "name" | "text" | "hybrid" | "positive" | "negative";
let active = false;
export function setMeasurement(enabled: boolean) {
  active = enabled;
}
export function track(event: EventName, value?: EventValue) {
  if (!active || typeof window === "undefined") return;
  // No arbitrary attributes: never send filenames, prompts, URLs or contents.
  void fetch("/api/events", {
    method: "POST",
    keepalive: true,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
    body: JSON.stringify({
      events: [{ id: crypto.randomUUID(), event, value: value ?? null }],
    }),
  }).catch(() => {});
}
