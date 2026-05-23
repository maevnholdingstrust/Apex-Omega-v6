import React from "react";
import { Archive, ExternalLink } from "lucide-react";
import { motion } from "framer-motion";
import { formatUsd } from "../lib/api";

export default function ArchivePanel({ cycles, onSelect }) {
  return (
    <div data-testid="archive-panel" className="glass rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-amber">
            <Archive className="w-3.5 h-3.5" />
            <span>05 · Archive</span>
          </div>
          <h3 className="text-lg font-semibold text-white/95 mt-1">
            Unified C1 + C2 opportunity cycles
          </h3>
        </div>
        <span className="text-[11px] text-white/40 numeric">{cycles.length} cycles</span>
      </div>

      <div className="overflow-x-auto max-h-[420px] overflow-y-auto rounded-lg">
        <table className="w-full text-[12px]">
          <thead className="sticky top-0 bg-obsidian-900/95 backdrop-blur z-10">
            <tr className="text-[10px] uppercase tracking-[0.18em] text-white/40">
              <th className="text-left px-3 py-2 font-medium">Cycle</th>
              <th className="text-left px-2 py-2 font-medium">Pair</th>
              <th className="text-right px-2 py-2 font-medium">Spread</th>
              <th className="text-left px-2 py-2 font-medium">C1</th>
              <th className="text-left px-2 py-2 font-medium">C2</th>
              <th className="text-right px-2 py-2 font-medium">C1 PnL</th>
              <th className="text-right px-2 py-2 font-medium">C2 PnL</th>
              <th className="text-right px-3 py-2 font-medium">Total</th>
              <th className="w-6"></th>
            </tr>
          </thead>
          <tbody>
            {cycles.length === 0 ? (
              <tr>
                <td colSpan={9} className="text-center text-white/30 py-10">
                  No cycles archived yet. Execute a pipeline to begin recording history.
                </td>
              </tr>
            ) : (
              cycles.map((c) => (
                <motion.tr
                  key={c.cycle_id}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  onClick={() => onSelect(c)}
                  data-testid={`cycle-row-${c.cycle_id}`}
                  className="cursor-pointer border-b border-white/[0.03] hover:bg-white/[0.025]"
                >
                  <td className="px-3 py-2 text-white/60 font-mono text-[10.5px]">{c.cycle_id}</td>
                  <td className="px-2 py-2 text-white/90 font-semibold">{c.pair}</td>
                  <td className="px-2 py-2 text-right numeric text-plasma-200/85">
                    {c.opportunity?.raw_spread_bps?.toFixed(1)}
                  </td>
                  <td className="px-2 py-2">
                    <span
                      className={`text-[10px] uppercase tracking-[0.18em] px-1.5 py-0.5 rounded border
                        ${c.c1?.status === "executed"
                          ? "bg-emerald/10 text-emerald border-emerald/30"
                          : "bg-crimson/10 text-crimson border-crimson/30"}`}
                    >
                      {c.c1?.status || "—"}
                    </span>
                  </td>
                  <td className="px-2 py-2">
                    <span
                      className={`text-[10px] uppercase tracking-[0.18em] px-1.5 py-0.5 rounded border
                        ${c.c2?.action === "MIRROR"
                          ? "bg-emerald/10 text-emerald border-emerald/30"
                          : c.c2?.action === "REVERSE"
                          ? "bg-violet-500/15 text-violet-300 border-violet-500/30"
                          : "bg-white/[0.04] text-white/50 border-white/10"}`}
                    >
                      {c.c2?.action || "—"}
                    </span>
                  </td>
                  <td
                    className={`px-2 py-2 text-right numeric ${
                      (c.c1_net_profit_usd ?? 0) > 0 ? "text-emerald" : "text-crimson"
                    }`}
                  >
                    {formatUsd(c.c1_net_profit_usd, 4)}
                  </td>
                  <td
                    className={`px-2 py-2 text-right numeric ${
                      (c.c2_net_profit_usd ?? 0) > 0
                        ? "text-emerald"
                        : (c.c2_net_profit_usd ?? 0) < 0
                        ? "text-crimson"
                        : "text-white/40"
                    }`}
                  >
                    {formatUsd(c.c2_net_profit_usd, 4)}
                  </td>
                  <td
                    className={`px-3 py-2 text-right numeric font-semibold ${
                      c.total_net_profit_usd > 0 ? "text-emerald" : "text-crimson"
                    }`}
                  >
                    {formatUsd(c.total_net_profit_usd, 4)}
                  </td>
                  <td className="pr-2">
                    <ExternalLink className="w-3 h-3 text-white/25" />
                  </td>
                </motion.tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
