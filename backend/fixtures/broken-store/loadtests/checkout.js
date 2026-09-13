import http from "k6/http";
import { check } from "k6";
import { Trend } from "k6/metrics";

const databaseQueries = new Trend("database_queries");
const isWarmup = __ENV.WARMUP === "1";

export const options = {
  vus: 1,
  iterations: isWarmup ? 5 : 20,
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "min", "max"],
};

export default function () {
  const response = http.post(`${__ENV.BASE_URL || "http://127.0.0.1:8000"}/checkout`);
  if (!isWarmup) {
    databaseQueries.add(Number(response.headers["X-Database-Query-Count"]));
  }
  check(response, { "checkout succeeds": (result) => result.status === 200 });
}
