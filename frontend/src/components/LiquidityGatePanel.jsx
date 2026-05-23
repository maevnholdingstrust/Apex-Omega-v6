import React from "react";
import { Database, ShieldCheck, ShieldX, Filter } from "lucide-react";
import axios from "axios";
import { api } from "../lib/api";

const FAMILY_COLOR = {
  v2: "#6DE7FF",
  v3: "#33D5FF",
  algebra: "#A78BFA",
  balancer: "#FFB020",
  curve: "#1FE08F",
};

const REJECT_TONE = {
  tvl_floor: "bg-crimson/10 text-crimson border-crimson/30",
  price_sanity: "bg-amber/10 text-amber border-amber/30",
  freshness: "bg-violet-500/10 text-violet-300 border-violet-500/30",
};

export default function LiquidityGatePanel() {
  const [data, setData] = React.useState(null);
  const [view, setView] = React.useState("eligible"); // eligible | rejected

  const load = React.useCallback(() => {
    api.get("/liquidity/gate").then((r) => setData(r.data)).catch(() => {});
  }, []);

  React.useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [load]);

  if (!data) {
    return (
      <div className="glass rounded-2xl p-5 h-full">
        <div className="text-white/30 text-[12px]">Loading liquidity gate…</div>
      </div>
    );
  }

  const { counters, eligible = [], rejected = [], config } = data;
  const eligiblePct = (counters.eligible / Math.max(counters.total, 1)) * 100;

  // Group eligible by pair
  const eligibleByPair = {};
  eligible.forEach((e) => {
    (eligibleByPair[e.pair] = eligibleByPair[e.pair] || []).push(e);
  });

  return (
    <div data-testid="liquidity-gate-panel" className="glass rounded-2xl p-5 h-full">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-plasma-200/80">
            <Filter className="w-3.5 h-3.5" />
            <span>00 · Liquidity Gate</span>
          </div>
          <h3 className="text-lg font-semibold text-white/95 mt-1">
            Pool eligibility · pre-discovery filter
          </h3>
          <p className="text-[11px] text-white/40 mt-0.5 max-w-[420px]">
            Enablement filter, not an execution gate. Pools must clear TVL,
            price-sanity, and freshness to enter the arb route search.
          </p>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-[0.18em] text-white/40">Eligible</div>
          <div className="font-display text-2xl text-gradient-cyan numeric">
            {counters.eligible}
            <span className="text-white/30 text-[14px] ml-1">/ {counters.total}</span>
          </div>
        </div>
      </div>

      {/* gate bar */}
      <div className="mt-1">
        <div className="h-1.5 rounded-full bg-white/[0.05] overflow-hidden flex">
          <div
            className="h-full bg-gradient-to-r from-plasma-400 to-emerald"
            style={{ width: `${eligiblePct}%` }}
          />
          <div
            className="h-full bg-gradient-to-r from-amber to-crimson"
            style={{ width: `${100 - eligiblePct}%` }}
          />
        </div>
        <div className="mt-2 flex flex-wrap gap-2 text-[10.5px]">
          <span className="text-white/45">
            TVL floor ${(config.min_tvl_usd / 1000).toFixed(0)}k:{" "}
            <span className="text-crimson numeric">{counters.tvl_fail} fail</span>
          </span>
          <span className="text-white/45">
            Price-sanity ±{(config.max_price_dev_pct * 100).toFixed(1)}%:{" "}
            <span className="text-amber numeric">{counters.price_sanity_fail} fail</span>
          </span>
          <span className="text-white/45">
            Freshness ≤{config.max_freshness_ms}ms:{" "}
            <span className="text-violet-300 numeric">{counters.freshness_fail} fail</span>
          </span>
        </div>
      </div>

      {/* toggle */}
      <div className="mt-4 inline-flex p-0.5 rounded-lg bg-white/[0.03] border border-white/[0.06] text-[11px]">
        <button
          data-testid="gate-view-eligible"
          onClick={() => setView("eligible")}
          className={`px-3 py-1 rounded-md transition ${
            view === "eligible" ? "bg-emerald/15 text-emerald" : "text-white/45"
          }`}
        >
          <ShieldCheck className="w-3 h-3 inline mr-1 -mt-0.5" />
          Eligible {counters.eligible}
        </button>
        <button
          data-testid="gate-view-rejected"
          onClick={() => setView("rejected")}
          className={`px-3 py-1 rounded-md transition ${
            view === "rejected" ? "bg-crimson/15 text-crimson" : "text-white/45"
          }`}
        >
          <ShieldX className="w-3 h-3 inline mr-1 -mt-0.5" />
          Rejected {counters.rejected}
        </button>
      </div>

      <div className="mt-3 max-h-[260px] overflow-y-auto pr-1">
        {view === "eligible" ? (
          <div className="space-y-2.5">
            {Object.entries(eligibleByPair).map(([pair, pls]) => (
              <div key={pair} className="border-l-2 border-emerald/30 pl-3">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-semibold text-white/90">{pair}</span>
                  <span className="text-[10px] text-white/40 numeric">
                    {pls.length} pools eligible
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {pls.map((p) => (
                    <span
                      key={p.pool_id}
                      title={`${p.venue} · ${p.family} · TVL $${p.tvl_usd.toLocaleString(undefined,{maximumFractionDigits:0})} · ${p.freshness_ms}ms`}
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border bg-white/[0.025]"
                      style={{
                        borderColor: FAMILY_COLOR[p.family] + "55",
                        color: FAMILY_COLOR[p.family],
                      }}
                    >
                      <span
                        className="w-1 h-1 rounded-full"
                        style={{ background: FAMILY_COLOR[p.family] }}
                      />
                      {p.venue}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="space-y-1.5">
            {rejected.length === 0 ? (
              <div className="text-[12px] text-white/35 py-4 text-center">
                All pools passed the gate.
              </div>
            ) : (
              rejected.map((r) => (
                <div
                  key={r.pool_id}
                  className="flex items-center justify-between px-2.5 py-1.5 rounded-lg bg-white/[0.02] border border-white/[0.04]"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ background: FAMILY_COLOR[r.family] }}
                    />
                    <span className="text-[11px] text-white/85 font-semibold truncate">
                      {r.pair}
                    </span>
                    <span className="text-[10.5px] text-white/45 truncate">{r.venue}</span>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {r.reject_reasons.map((reason) => (
                      <span
                        key={reason}
                        className={`text-[9.5px] uppercase tracking-[0.16em] px-1.5 py-0.5 rounded border ${REJECT_TONE[reason]}`}
                      >
                        {reason.replace("_", " ")}
                      </span>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
