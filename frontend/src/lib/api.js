import axios from "axios";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
export const api = axios.create({
  baseURL: `${BACKEND}/api`,
  timeout: 30000,
});

export const fetchStatus = () => api.get("/status").then((r) => r.data);
export const fetchTelemetry = () => api.get("/telemetry").then((r) => r.data);
export const fetchGraph = () => api.get("/graph").then((r) => r.data);
export const fetchOpportunities = (limit = 30) =>
  api.get("/opportunities", { params: { limit } }).then((r) => r.data);
export const triggerScan = (trade_size_usd, min_spread_bps) =>
  api
    .post("/discovery/scan", { trade_size_usd, min_spread_bps })
    .then((r) => r.data);
export const fetchRisk = (id) => api.get(`/risk/${id}`).then((r) => r.data);
export const runPipeline = (opp_id) =>
  api.post("/pipeline/run", { opp_id }).then((r) => r.data);
export const fetchCycles = (limit = 50) =>
  api.get("/cycles", { params: { limit } }).then((r) => r.data);
export const fetchCycle = (id) => api.get(`/cycles/${id}`).then((r) => r.data);

export const formatUsd = (n, dp = 2) => {
  if (n == null || isNaN(n)) return "—";
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(dp)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(dp)}k`;
  return `${sign}$${abs.toFixed(dp)}`;
};
export const formatBps = (n) => (n == null ? "—" : `${n.toFixed(1)} bps`);
export const shortHash = (h) =>
  !h ? "—" : `${h.slice(0, 6)}…${h.slice(-4)}`;
