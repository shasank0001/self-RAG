import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 5,
  duration: "3m",
  thresholds: {
    http_req_failed: ["rate<0.03"],
    http_req_duration: ["p(95)<1500"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const BIN_ID = __ENV.BIN_ID || "00000000-0000-0000-0000-000000000000";
const TOKEN = __ENV.AUTH_TOKEN || "";

export default function () {
  const payload = JSON.stringify({
    text: `Load test chunk ${__VU}-${__ITER}: fallback-safe ingestion throughput check.`,
    source_name: `perf-${__VU}-${__ITER}.txt`,
  });

  const res = http.post(`${BASE_URL}/api/v1/bins/${BIN_ID}/items/text`, payload, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
    },
    tags: { scenario: "ingestion_pipeline" },
  });

  check(res, {
    "status accepted": (r) => r.status === 202,
    "job queued": (r) => r.body && r.body.includes('"status":"queued"'),
  });

  sleep(0.2);
}
