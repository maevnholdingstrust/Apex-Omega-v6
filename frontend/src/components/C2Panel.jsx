import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Activity, ArrowRight, RotateCcw, Ban } from "lucide-react";
import { formatUsd, shortHash } from "../lib/api";

const ACTION_META = {
  MIRROR:     { icon: ArrowRight, tone: "emerald", desc: "Same pair · same direction · residual edge after C1" },
  REVERSE:    { icon: RotateCcw,  tone: "violet",  desc: "Same pair · opposite direction · rebound from C1 impact" },
  DO_NOTHING: { icon: Ban,        tone: "white/50",desc: "All EV ≤ 0 · explicit no-op · no TX submitted" },
};

function CandidateCard({ c, isSelected }) {
  const meta = ACTION_META[c.action];
  const Icon = meta.icon;
  const toneCls = {
    emerald: "border-emerald/30 bg-emerald/[0.05] text-emerald",
    violet: "border-violet-500/30 bg-violet-500/[0.06] text-violet-300",
    "white/50": "border-white/10 bg-white/[0.02] text-white/45",
  }[meta.tone];

  return (
    <div
      className={`relative rounded-xl border p-3 transition-all
        ${isSelected ? toneCls + " ring-1 ring-current/30" : "border-white/[0.05] bg-white/[0.015] text-white/50"}`}
    >
      {isSelected && (
        <span className="absolute -top-2 right-3 text-[9px] uppercase tracking-[0.2em] px-1.5 py-0.5 rounded bg-obsidian-950 border border-current text-current">
          selected
        </span>
      )}
      <div className="flex items-center gap-2">
        <Icon className="w-3.5 h-3.5" />
        <span className="font-semibold text-[12px] tracking-wide">{c.action}</span>
      </div>
      <p className="text-[10.5px] mt-1 text-white/45">{meta.desc}</p>
      <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10.5px]">
        <span className="text-white/40">EV</span>
        <span className={`text-right numeric ${c.expected_ev_usdc > 0 ? "text-emerald" : "text-white/40"}`}>
          ${c.expected_ev_usdc.toFixed(4)}
        </span>
        <span className="text-white/40">Net</span>
        <span className={`text-right numeric ${c.expected_net_usd > 0 ? "text-emerald" : "text-white/40"}`}>
          {formatUsd(c.expected_net_usd, 4)}
        </span>
        <span className="text-white/40">Buf</span>
        <span className="text-right numeric text-violet-300">
          {(c.selected_buffer * 100).toFixed(2)}%
        </span>
      </div>
      <div className="mt-2 text-[9.5px] font-mono text-white/30 truncate">
        leaf {shortHash(c.merkle_leaf)}
      </div>
    </div>
  );
}

export default function C2Panel({ cycle }) {
  const c2 = cycle?.c2;
  const selectedAction = c2?.action;

  return (
    <div data-testid="c2-panel" className="glass rounded-2xl p-5 h-full min-h-[360px]">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-violet-300">
            <Activity className="w-3.5 h-3.5" />
            <span>03 · C2 Surgeon</span>
            <span className="text-white/20">·</span>
            <span className="text-white/50">TX 2 of 2 · activated by C1 fill</span>
          </div>
          <h3 className="text-lg font-semibold text-white/95 mt-1">
            Ultimate Arbitrage Executor · Block N+1 ·{" "}
            <span className="text-violet-300">same token as C1 · {cycle?.pair || "—"}</span>
          </h3>
        </div>
        {c2 && (
          <span
            className={`text-[10px] uppercase tracking-[0.18em] px-2 py-0.5 rounded border
              ${c2.action === "DO_NOTHING"
                ? "bg-white/[0.04] text-white/50 border-white/10"
                : c2.status === "executed"
                ? "bg-emerald/10 text-emerald border-emerald/30"
                : "bg-crimson/10 text-crimson border-crimson/30"}`}
          >
            {c2.action} {c2.status ? `· ${c2.status}` : ""}
          </span>
        )}
      </div>

      {!c2 ? (
        <div className="text-white/30 text-[12px] py-12 text-center">
          C2 awaits post-C1 state…
        </div>
      ) : (
        <AnimatePresence mode="wait">
          <motion.div key={cycle.cycle_id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
            <div className="grid grid-cols-3 gap-2.5">
              {c2.candidates?.map((c) => (
                <CandidateCard key={c.action} c={c} isSelected={c.action === selectedAction} />
              ))}
            </div>

            <div className="mt-4 grid grid-cols-2 gap-x-5 gap-y-1.5 text-[12px]">
              <Item label="Merkle root" value={shortHash(c2.merkle_root)} tone="violet" />
              <Item label="Relay" value={c2.relay || "—"} />
              <Item label="Bundle" value={shortHash(c2.bundle_hash)} tone="cyan" />
              <Item label="Block target" value={c2.block_target ? `#${c2.block_target}` : "—"} />
              <Item label="Latency" value={c2.latency_ms ? `${c2.latency_ms.toFixed(1)} ms` : "—"} />
              <Item
                label="Actual net"
                value={formatUsd(c2.actual_net_profit_usd, 4)}
                tone={c2.actual_net_profit_usd > 0 ? "emerald" : "crimson"}
              />
            </div>

            {c2.selected?.proof?.length > 0 && (
              <div className="mt-3 text-[10px] uppercase tracking-[0.2em] text-white/40 mb-1">
                Merkle proof
              </div>
            )}
            <div className="flex flex-wrap gap-1.5">
              {c2.selected?.proof?.map((p, i) => (
                <span
                  key={i}
                  className="px-1.5 py-0.5 text-[9.5px] font-mono bg-violet-500/10 border border-violet-500/20 text-violet-300/80 rounded"
                >
                  {shortHash(p)}
                </span>
              ))}
            </div>
          </motion.div>
        </AnimatePresence>
      )}
    </div>
  );
}

function Item({ label, value, tone }) {
  const map = {
    cyan: "text-plasma-200",
    violet: "text-violet-300",
    emerald: "text-emerald",
    crimson: "text-crimson",
  };
  return (
    <div className="flex items-center justify-between border-b border-white/[0.04] py-1.5">
      <span className="text-[10.5px] uppercase tracking-[0.18em] text-white/40">{label}</span>
      <span className={`numeric text-[12px] ${map[tone] || "text-white/90"}`}>{value}</span>
    </div>
  );
}
