import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Skull, Zap, AlertTriangle, Loader2, Activity } from "lucide-react";
import { toast } from "sonner";
import { api, formatUsd, shortHash } from "../lib/api";

function HFBadge({ hf }) {
  const ok = hf >= 1.0;
  const critical = hf < 0.92;
  const tone = ok
    ? "bg-emerald/10 text-emerald border-emerald/30"
    : critical
    ? "bg-crimson/15 text-crimson border-crimson/40"
    : "bg-amber/15 text-amber border-amber/40";
  return (
    <span className={`numeric text-[11px] px-1.5 py-0.5 rounded border ${tone}`}>
      HF {hf.toFixed(3)}
    </span>
  );
}

export default function LiquidationsPanel() {
  const [data, setData] = React.useState(null);
  const [executing, setExecuting] = React.useState(null);
  const [lastResult, setLastResult] = React.useState(null);
  const [history, setHistory] = React.useState([]);
  const [filter, setFilter] = React.useState("eligible"); // eligible | all

  const load = React.useCallback(async () => {
    try {
      const [scan, hist] = await Promise.all([
        api.get("/liquidations/scan"),
        api.get("/liquidations/history", { params: { limit: 25 } }),
      ]);
      setData(scan.data);
      setHistory(hist.data.liquidations || []);
    } catch (e) {
      /* swallow */
    }
  }, []);

  React.useEffect(() => {
    load();
    const id = setInterval(load, 3500);
    return () => clearInterval(id);
  }, [load]);

  const doExecute = async (pos) => {
    setExecuting(pos.position_id);
    try {
      const r = await api.post("/liquidations/execute", {
        position_id: pos.position_id,
      });
      setLastResult(r.data);
      load();
      if (r.data.status === "executed") {
        toast.success(
          `Liquidation executed · seized ${formatUsd(r.data.seized_collateral_usd, 0)} · net ${formatUsd(r.data.actual_net_bonus_usd, 2)}`,
        );
      } else {
        toast.error(`Liquidation ${r.data.status}: ${r.data.result?.reason || "—"}`);
      }
    } catch (e) {
      toast.error("Liquidation failed: " + (e.response?.data?.detail || e.message));
    } finally {
      setExecuting(null);
    }
  };

  if (!data) {
    return (
      <div className="glass rounded-2xl p-5 h-full">
        <div className="text-white/30 text-[12px]">Loading positions…</div>
      </div>
    );
  }

  const positions = filter === "eligible"
    ? data.positions.filter((p) => p.eligible)
    : data.positions;

  return (
    <div data-testid="liquidations-panel" className="glass rounded-2xl p-5 h-full">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-crimson">
            <Skull className="w-3.5 h-3.5" />
            <span>Strategy · Liquidations</span>
          </div>
          <h3 className="text-lg font-semibold text-white/95 mt-1">
            Aave V3 borrowers · health factor &lt; 1.0
          </h3>
          <p className="text-[11px] text-white/40 mt-0.5">
            Repay up to {(data.close_factor * 100).toFixed(0)}% of debt · seize
            collateral + liquidation bonus
          </p>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-[0.18em] text-white/40">
            Liquidatable
          </div>
          <div className="font-display text-2xl text-gradient-ember numeric">
            {data.eligible_count}
            <span className="text-white/30 text-[14px] ml-1">/ {data.count}</span>
          </div>
        </div>
      </div>

      <div className="inline-flex p-0.5 rounded-lg bg-white/[0.03] border border-white/[0.06] text-[11px] mb-2">
        <button
          data-testid="liq-filter-eligible"
          onClick={() => setFilter("eligible")}
          className={`px-3 py-1 rounded-md transition ${
            filter === "eligible" ? "bg-crimson/15 text-crimson" : "text-white/45"
          }`}
        >
          Liquidatable {data.eligible_count}
        </button>
        <button
          data-testid="liq-filter-all"
          onClick={() => setFilter("all")}
          className={`px-3 py-1 rounded-md transition ${
            filter === "all" ? "bg-white/[0.06] text-white/85" : "text-white/45"
          }`}
        >
          All {data.count}
        </button>
      </div>

      <div className="max-h-[300px] overflow-y-auto pr-1 space-y-1.5">
        <AnimatePresence>
          {positions.length === 0 ? (
            <div className="text-[12px] text-white/35 py-6 text-center">
              No positions match. Market moved out of liquidation zone.
            </div>
          ) : (
            positions.map((p) => (
              <motion.div
                key={p.position_id}
                layout
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                data-testid={`liq-row-${p.position_id}`}
                className={`px-3 py-2 rounded-xl border flex items-center gap-3
                  ${p.eligible
                    ? "bg-crimson/[0.04] border-crimson/20"
                    : "bg-white/[0.015] border-white/[0.05]"}`}
              >
                <div className="flex flex-col gap-1 min-w-[88px]">
                  <HFBadge hf={p.health_factor} />
                  <span className="text-[9.5px] font-mono text-white/35 truncate">
                    {p.borrower.slice(0, 7)}…{p.borrower.slice(-3)}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-semibold text-white/90">
                      {p.collateral_asset}
                    </span>
                    <span className="text-white/30 text-[10px]">→</span>
                    <span className="text-[12px] text-white/65">{p.debt_asset}</span>
                    <span className="text-[9.5px] uppercase tracking-[0.18em] text-white/35 ml-1">
                      {p.liquidation_bonus_bps}bps bonus
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-[10.5px] text-white/50 mt-0.5 numeric">
                    <span>Coll ${p.collateral_usd.toLocaleString(undefined,{maximumFractionDigits:0})}</span>
                    <span className="text-white/25">·</span>
                    <span>Debt ${p.debt_usd.toLocaleString(undefined,{maximumFractionDigits:0})}</span>
                    <span className="text-white/25">·</span>
                    <span className="text-amber">
                      Repay {formatUsd(p.max_repay_usd, 0)}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <div className="text-right">
                    <div className="text-[9.5px] uppercase text-white/35 tracking-[0.16em]">
                      Net bonus
                    </div>
                    <div
                      className={`numeric text-[13px] font-semibold ${
                        p.net_bonus_usd > 0 ? "text-emerald" : "text-white/40"
                      }`}
                    >
                      {formatUsd(p.net_bonus_usd, 2)}
                    </div>
                  </div>
                  <button
                    data-testid={`liq-execute-${p.position_id}`}
                    disabled={!p.executable || executing === p.position_id}
                    onClick={() => doExecute(p)}
                    className={`px-2.5 py-1.5 rounded-lg text-[11px] font-semibold flex items-center gap-1.5 transition-all
                      ${p.executable
                        ? "bg-gradient-to-r from-crimson/30 to-ember/30 border border-crimson/40 text-white hover:from-crimson/45 hover:to-ember/45"
                        : "bg-white/[0.03] text-white/30 border border-white/10 cursor-not-allowed"}
                      disabled:opacity-60`}
                  >
                    {executing === p.position_id ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Zap className="w-3 h-3" />
                    )}
                    Liquidate
                  </button>
                </div>
              </motion.div>
            ))
          )}
        </AnimatePresence>
      </div>

      {/* Recent history strip */}
      {history.length > 0 && (
        <div className="mt-4 pt-3 border-t border-white/[0.05]">
          <div className="text-[10px] uppercase tracking-[0.22em] text-white/40 mb-2 flex items-center gap-2">
            <Activity className="w-3 h-3" /> Recent executions
          </div>
          <div className="flex gap-1.5 overflow-x-auto pb-1">
            {history.slice(0, 12).map((h) => (
              <div
                key={h.liquidation_id}
                title={`${h.collateral_asset}→${h.debt_asset} · HF ${h.health_factor_at_exec.toFixed(3)}`}
                className={`shrink-0 px-2.5 py-1.5 rounded-lg border min-w-[120px]
                  ${h.status === "executed"
                    ? "bg-emerald/[0.06] border-emerald/25"
                    : "bg-crimson/[0.06] border-crimson/25"}`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[9.5px] uppercase tracking-[0.18em] text-white/45">
                    {h.collateral_asset}/{h.debt_asset}
                  </span>
                  <span
                    className={`text-[9.5px] uppercase tracking-[0.18em] ${
                      h.status === "executed" ? "text-emerald" : "text-crimson"
                    }`}
                  >
                    {h.status === "executed" ? "exec" : h.status.slice(0, 5)}
                  </span>
                </div>
                <div
                  className={`numeric text-[12.5px] font-semibold mt-0.5 ${
                    (h.actual_net_bonus_usd || 0) > 0
                      ? "text-emerald"
                      : "text-crimson"
                  }`}
                >
                  {formatUsd(h.actual_net_bonus_usd, 2)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
