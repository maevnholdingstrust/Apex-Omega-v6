import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Crosshair, Check, X, AlertTriangle } from "lucide-react";
import { formatUsd, shortHash } from "../lib/api";

function GateRow({ check }) {
  return (
    <div className="flex items-center justify-between py-1 text-[11px]">
      <span className="text-white/60 font-mono">{check.gate}</span>
      <span className="flex items-center gap-2">
        <span className="text-white/45 numeric">
          {typeof check.value === "number" ? check.value.toFixed(4) : check.value}
        </span>
        {check.pass ? (
          <Check className="w-3 h-3 text-emerald" />
        ) : (
          <X className="w-3 h-3 text-crimson" />
        )}
      </span>
    </div>
  );
}

export default function C1Panel({ cycle }) {
  const c1 = cycle?.c1;

  return (
    <div data-testid="c1-panel" className="glass rounded-2xl p-5 h-full min-h-[360px]">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-plasma-200/80">
            <Crosshair className="w-3.5 h-3.5" />
            <span>02 · C1 Aggressor</span>
          </div>
          <h3 className="text-lg font-semibold text-white/95 mt-1">
            Institutional Executor · Block N
          </h3>
        </div>
        {c1 && (
          <span
            className={`text-[10px] uppercase tracking-[0.18em] px-2 py-0.5 rounded border
              ${c1.status === "executed"
                ? "bg-emerald/10 text-emerald border-emerald/30"
                : c1.status === "rejected"
                ? "bg-amber/10 text-amber border-amber/30"
                : "bg-crimson/10 text-crimson border-crimson/30"}`}
          >
            {c1.status}
          </span>
        )}
      </div>

      {!c1 ? (
        <div className="text-white/30 text-[12px] py-12 text-center">
          C1 awaits pipeline trigger…
        </div>
      ) : (
        <AnimatePresence mode="wait">
          <motion.div
            key={cycle.cycle_id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <div className="grid grid-cols-2 gap-x-5 gap-y-2 text-[12px]">
              <Item label="Envelope" value={shortHash(c1.envelope?.envelope_hash)} tone="cyan" />
              <Item label="Relay" value={c1.relay || "—"} />
              <Item label="Bundle" value={shortHash(c1.bundle_hash)} tone="violet" />
              <Item label="Block target" value={c1.block_target ? `#${c1.block_target}` : "—"} />
              <Item label="Latency" value={c1.latency_ms ? `${c1.latency_ms.toFixed(1)} ms` : "—"} />
              <Item label="Buffer" value={`${(cycle.opportunity?.selected_buffer * 100).toFixed(3)}%`} tone="violet" />
              <Item
                label="Expected net"
                value={formatUsd(c1.expected_net_profit_usd ?? cycle.opportunity?.net_profit_usd, 4)}
              />
              <Item
                label="Actual net"
                value={formatUsd(c1.actual_net_profit_usd, 4)}
                tone={c1.actual_net_profit_usd > 0 ? "emerald" : "crimson"}
              />
            </div>

            <div className="mt-4">
              <div className="text-[10px] uppercase tracking-[0.2em] text-white/40 mb-1.5">
                EVM Mirror gates
              </div>
              <div className="space-y-0.5 bg-white/[0.02] rounded-lg p-2.5 border border-white/[0.04]">
                {c1.mirror?.checks?.map((c, i) => <GateRow key={i} check={c} />)}
              </div>
            </div>

            <div className="mt-3">
              <div className="text-[10px] uppercase tracking-[0.2em] text-white/40 mb-1.5">
                Route envelope · v{c1.envelope?.version}
              </div>
              <div className="space-y-1">
                {c1.envelope?.steps?.map((s, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-white/[0.025] border border-white/[0.05] text-[11.5px]"
                  >
                    <span className="text-white/70">
                      <span className="text-white/35 mr-1">{i + 1}.</span>
                      {s.venue} <span className="text-white/30">·</span> {s.protocol}
                    </span>
                    <span className="text-white/55 numeric">
                      {s.approve_token} → {s.output_token}
                    </span>
                    <span className="text-emerald/90 numeric">
                      ≥ {formatUsd(s.min_amount_out_usd, 2)}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {c1.reason && (
              <div className="mt-3 text-[11.5px] flex items-center gap-1.5 text-amber">
                <AlertTriangle className="w-3 h-3" /> {c1.reason}
              </div>
            )}
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
