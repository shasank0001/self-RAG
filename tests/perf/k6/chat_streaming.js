import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 10,
  duration: "2m",
  thresholds: {
    http_req_failed: ["rate<0.02"],
    http_req_duration: ["p(95)<2500"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const SESSION_ID = __ENV.SESSION_ID || "00000000-0000-0000-0000-000000000000";
const TOKEN = __ENV.AUTH_TOKEN || "";

export default function () {
  const payload = JSON.stringify({ message: "Summarize fallback behavior under partial outage." });
  const res = http.post(`${BASE_URL}/api/v1/chat/sessions/${SESSION_ID}/message`, payload, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
      Accept: "text/event-stream",
    },
    tags: { scenario: "chat_streaming" },
  });

  check(res, {
    "status is 200": (r) => r.status === 200,
    "contains done event": (r) => r.body && r.body.includes("event: done"),
    "contains citations event": (r) => r.body && r.body.includes("event: citations"),
    "no stream error": (r) => !r.body || !r.body.includes("event: error"),
  });

  sleep(1);
}
