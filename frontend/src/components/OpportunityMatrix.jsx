import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Search, ArrowDownUp, RefreshCw, ChevronRight } from "lucide-react";
import { formatUsd, formatBps } from "../lib/api";

function Pill({ children, tone = "neutral" }) {
  const map = {
    neutral: "bg-white/[0.04] text-white/70 border-white/10",
    cyan: "bg-plasma-400/10 text-plasma-200 border-plasma-300/30",
    violet: "bg-violet-500/15 text-[#C9B3FF] border-violet-500/30",
    emerald: "bg-emerald/10 text-emerald border-emerald/30",
    crimson: "bg-crimson/10 text-crimson border-crimson/30",
    amber: "bg-amber/10 text-amber border-amber/30",
  };
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono tracking-tight border ${map[tone]}`}>
      {children}
    </span>
  );
}

const familyTone = {
  v2: "neutral",
  v3: "cyan",
  algebra: "violet",
  balancer: "amber",
  curve: "emerald",
};

export default function OpportunityMatrix({
  opportunities,
  selected,
  onSelect,
  tradeSize,
  setTradeSize,
  minSpreadBps,
  setMinSpreadBps,
  scanning,
  onScan,
  autoScan,
}) {
  return (
    <div
      data-testid="opportunity-matrix"
      className="glass rounded-2xl overflow-hidden"
    >
      {/* header */}
      <div className="flex items-center justify-between px-5 pt-4 pb-3 border-b border-white/[0.05]">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.24em] text-white/40">
            <Search className="w-3.5 h-3.5" />
            <span>01 · Discovery</span>
            <span className="text-white/20">/</span>
            <span className="text-plasma-200/80">Opportunity Matrix</span>
          </div>
          <h2 className="text-xl font-semibold text-white/95 mt-1 tracking-tight">
            Polygon liquidity graph · cross-DEX arbitrage
          </h2>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-[11px] text-white/55">
            <span className="uppercase tracking-[0.18em]">Size</span>
            <input
              data-testid="trade-size-input"
              type="number"
              value={tradeSize}
              onChange={(e) => setTradeSize(Number(e.target.value) || 0)}
              className="w-24 px-2 py-1 bg-obsidian-800/80 border border-white/[0.08] rounded text-right numeric text-white/90 focus:outline-none focus:border-plasma-300/60"
            />
          </label>
          <label className="flex items-center gap-2 text-[11px] text-white/55">
            <span className="uppercase tracking-[0.18em]">Min bps</span>
            <input
              data-testid="min-spread-input"
              type="number"
              step={0.5}
              value={minSpreadBps}
              onChange={(e) => setMinSpreadBps(Number(e.target.value) || 0)}
              className="w-16 px-2 py-1 bg-obsidian-800/80 border border-white/[0.08] rounded text-right numeric text-white/90 focus:outline-none focus:border-plasma-300/60"
            />
          </label>
          <button
            data-testid="scan-btn"
            onClick={onScan}
            disabled={scanning}
            className="px-3 py-1.5 rounded-full bg-gradient-to-r from-plasma-400/25 to-violet-500/25 border border-plasma-300/40 text-plasma-100 text-[12px] font-semibold tracking-wide flex items-center gap-1.5 hover:from-plasma-400/35 hover:to-violet-500/35 disabled:opacity-60"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${scanning ? "animate-spin" : ""}`} />
            {scanning ? "Scanning" : "Scan now"}
          </button>
        </div>
      </div>

      {/* table */}
      <div className="overflow-x-auto max-h-[560px] overflow-y-auto">
        <table className="w-full text-[12px]">
          <thead className="sticky top-0 z-10 bg-obsidian-900/95 backdrop-blur-md">
            <tr className="text-[10px] uppercase tracking-[0.18em] text-white/40">
              <th className="text-left px-4 py-2 font-medium">Pair</th>
              <th className="text-left px-2 py-2 font-medium">Buy</th>
              <th className="text-left px-2 py-2 font-medium">Sell</th>
              <th className="text-right px-2 py-2 font-medium">Spread</th>
              <th className="text-right px-2 py-2 font-medium">Gross</th>
              <th className="text-right px-2 py-2 font-medium">Net</th>
              <th className="text-right px-2 py-2 font-medium">EV</th>
              <th className="text-right px-2 py-2 font-medium">Buffer</th>
              <th className="text-right px-4 py-2 font-medium">P(fill)</th>
              <th className="w-6" />
            </tr>
          </thead>
          <tbody>
            <AnimatePresence>
              {opportunities.length === 0 ? (
                <tr>
                  <td
                    colSpan={10}
                    className="text-center text-white/30 py-12 text-[12px]"
                  >
                    {scanning ? "Scanning Polygon liquidity graph…" : "No opportunities above threshold. Lower min-bps or trigger a scan."}
                  </td>
                </tr>
              ) : (
                opportunities.map((o) => {
                  const isSel = selected?.opp_id === o.opp_id;
                  const netPos = o.net_profit_usd > 0;
                  const evPos = o.selected_ev_usdc > 0;
                  return (
                    <motion.tr
                      key={o.opp_id}
                      layout
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      onClick={() => onSelect(o)}
                      data-testid={`opportunity-row-${o.opp_id}`}
                      className={`cursor-pointer border-b border-white/[0.03] transition-colors
                        ${isSel ? "bg-plasma-400/[0.08] border-plasma-300/30" : "hover:bg-white/[0.025]"}`}
                    >
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-white/95">{o.pair}</span>
                          {o.executable && (
                            <span className="text-[9px] uppercase tracking-[0.18em] text-emerald/90 bg-emerald/10 px-1.5 py-0.5 rounded border border-emerald/25">
                              exec
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-2 py-2.5">
                        <div className="flex items-center gap-1.5">
                          <span className="text-white/85 text-[11px]">{o.buy_venue}</span>
                          <Pill tone={familyTone[o.buy_family] || "neutral"}>{o.buy_family}</Pill>
                        </div>
                      </td>
                      <td className="px-2 py-2.5">
                        <div className="flex items-center gap-1.5">
                          <span className="text-white/85 text-[11px]">{o.sell_venue}</span>
                          <Pill tone={familyTone[o.sell_family] || "neutral"}>{o.sell_family}</Pill>
                        </div>
                      </td>
                      <td className="px-2 py-2.5 text-right numeric text-plasma-200/95">
                        {o.raw_spread_bps.toFixed(1)}
                      </td>
                      <td className="px-2 py-2.5 text-right numeric text-white/75">
                        {formatUsd(o.gross_profit_usd, 2)}
                      </td>
                      <td className={`px-2 py-2.5 text-right numeric font-semibold ${netPos ? "text-emerald" : "text-crimson"}`}>
                        {formatUsd(o.net_profit_usd, 2)}
                      </td>
                      <td className={`px-2 py-2.5 text-right numeric ${evPos ? "text-emerald" : "text-white/40"}`}>
                        {formatUsd(o.selected_ev_usdc, 2)}
                      </td>
                      <td className="px-2 py-2.5 text-right numeric text-violet-300">
                        {(o.selected_buffer * 100).toFixed(2)}%
                      </td>
                      <td className="px-4 py-2.5 text-right numeric text-white/70">
                        {(o.execution_probability * 100).toFixed(0)}%
                      </td>
                      <td className="text-white/30 pr-3">
                        <ChevronRight className={`w-3.5 h-3.5 transition-transform ${isSel ? "translate-x-0.5 text-plasma-200" : ""}`} />
                      </td>
                    </motion.tr>
                  );
                })
              )}
            </AnimatePresence>
          </tbody>
        </table>
      </div>
    </div>
  );
}
