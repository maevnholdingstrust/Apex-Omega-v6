import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Target, Zap, Loader2 } from "lucide-react";
import { formatUsd } from "../lib/api";

function KV({ label, value, accent, mono = true }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-white/[0.04] last:border-0">
      <span className="text-[11px] uppercase tracking-[0.16em] text-white/45">
        {label}
      </span>
      <span className={`text-[12.5px] ${mono ? "numeric" : ""} ${accent || "text-white/90"}`}>
        {value}
      </span>
    </div>
  );
}

export default function OpportunityDetail({ opp, onExecute, executing }) {
  if (!opp) {
    return (
      <div data-testid="opportunity-detail-empty" className="glass rounded-2xl p-6 h-full min-h-[480px] flex flex-col items-center justify-center text-center">
        <Target className="w-8 h-8 text-white/15 mb-3" strokeWidth={1.5} />
        <p className="text-white/40 text-[13px] max-w-[220px]">
          Select an opportunity from the matrix to inspect the route and route envelope.
        </p>
      </div>
    );
  }

  return (
    <motion.div
      key={opp.opp_id}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      data-testid="opportunity-detail"
      className="gradient-border p-[1px] h-full"
    >
      <div className="rounded-[18px] glass-strong p-5 h-full flex flex-col">
        <div className="flex items-start justify-between">
          <div>
            <div className="text-[10px] uppercase tracking-[0.24em] text-plasma-200/80 mb-1">
              Selected · {opp.opp_id}
            </div>
            <h3 className="font-display text-3xl italic text-white">
              {opp.pair}
            </h3>
            <div className="text-[12px] text-white/55 mt-0.5">
              {opp.buy_venue} <span className="text-white/30 mx-1">→</span> {opp.sell_venue}
            </div>
          </div>
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-[0.2em] text-white/40">Raw spread</div>
            <div className="font-display text-3xl text-gradient-cyan numeric">
              {opp.raw_spread_bps.toFixed(1)}
              <span className="text-[11px] ml-1 align-top">bps</span>
            </div>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-x-5">
          <KV label="Buy USD" value={`$${opp.buy_price_usd.toFixed(6)}`} />
          <KV label="Sell USD" value={`$${opp.sell_price_usd.toFixed(6)}`} />
          <KV label="Trade size" value={formatUsd(opp.trade_size_usd, 0)} />
          <KV label="Block" value={`#${opp.block?.toLocaleString()}`} />
          <KV label="Gross" value={formatUsd(opp.gross_profit_usd, 4)} accent="text-white/90" />
          <KV
            label="Net"
            value={formatUsd(opp.net_profit_usd, 4)}
            accent={opp.net_profit_usd > 0 ? "text-emerald" : "text-crimson"}
          />
          <KV label="Flash fee" value={`${opp.flash_provider} · ${opp.flash_fee_bps}bps`} accent="text-white/65" mono={false} />
          <KV label="Gas est." value={`$${opp.gas_cost_usd.toFixed(3)}`} accent="text-amber" />
          <KV label="Base buffer" value={`${(opp.base_buffer * 100).toFixed(3)}%`} accent="text-violet-300" />
          <KV label="Selected buf" value={`${(opp.selected_buffer * 100).toFixed(3)}%`} accent="text-plasma-200" />
          <KV
            label="EV (USDC)"
            value={formatUsd(opp.selected_ev_usdc, 4)}
            accent={opp.selected_ev_usdc > 0 ? "text-emerald" : "text-crimson"}
          />
          <KV
            label="P(fill)"
            value={`${(opp.execution_probability * 100).toFixed(1)}%`}
            accent="text-white/85"
          />
        </div>

        <div className="mt-auto pt-4">
          <button
            data-testid="execute-pipeline-btn"
            disabled={executing || !opp.executable}
            onClick={onExecute}
            className={`w-full relative rounded-xl px-4 py-3 font-semibold text-[13px] tracking-wide
              flex items-center justify-center gap-2 transition-all
              ${opp.executable
                ? "bg-gradient-to-r from-plasma-400 via-plasma-300 to-violet-500 text-obsidian-950 hover:shadow-[0_0_30px_-4px_rgba(0,190,236,0.55)]"
                : "bg-white/[0.04] text-white/30 border border-white/10 cursor-not-allowed"}
              disabled:opacity-70`}
          >
            {executing ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Executing pipeline…
              </>
            ) : opp.executable ? (
              <>
                <Zap className="w-4 h-4" />
                Run Pipeline · C1 → C2 → Submit
              </>
            ) : (
              "Below execution threshold"
            )}
          </button>
          <div className="text-[10.5px] text-white/35 text-center mt-2 tracking-wide">
            Discovery → EVM Mirror → C1 Aggressor → C2 Surgeon (Merkle) → Titan Submit → Archive
          </div>
        </div>
      </div>
    </motion.div>
  );
}
