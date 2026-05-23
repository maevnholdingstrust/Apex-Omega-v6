import React from "react";
import { motion } from "framer-motion";
import { SendHorizonal, Sparkles } from "lucide-react";
import { formatUsd, shortHash } from "../lib/api";

function BundleCard({ phase, payload, tone }) {
  const status = payload?.status || "—";
  return (
    <div
      className={`relative rounded-xl p-3 border ${
        tone === "cyan"
          ? "bg-plasma-400/[0.06] border-plasma-300/25"
          : "bg-violet-500/[0.06] border-violet-500/25"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className={`text-[10px] uppercase tracking-[0.22em] ${tone === "cyan" ? "text-plasma-200" : "text-violet-300"}`}>
          {phase}
        </span>
        <span
          className={`text-[10px] uppercase px-1.5 py-0.5 rounded border ${
            status === "executed"
              ? "bg-emerald/10 text-emerald border-emerald/30"
              : status === "reverted"
              ? "bg-crimson/10 text-crimson border-crimson/30"
              : "bg-white/[0.04] text-white/50 border-white/10"
          }`}
        >
          {status}
        </span>
      </div>
      <div className="mt-2 space-y-1 text-[11px]">
        <div className="flex justify-between"><span className="text-white/45">Relay</span><span className="text-white/80">{payload?.relay || "—"}</span></div>
        <div className="flex justify-between"><span className="text-white/45">Bundle</span><span className="numeric text-white/80">{shortHash(payload?.bundle_hash)}</span></div>
        <div className="flex justify-between"><span className="text-white/45">Block</span><span className="numeric text-white/80">{payload?.block_target ? `#${payload.block_target}` : "—"}</span></div>
        <div className="flex justify-between"><span className="text-white/45">Latency</span><span className="numeric text-white/80">{payload?.latency_ms ? `${payload.latency_ms.toFixed(1)}ms` : "—"}</span></div>
        <div className="flex justify-between border-t border-white/[0.05] pt-1.5 mt-1.5">
          <span className="text-white/45">Net</span>
          <span className={`numeric font-semibold ${payload?.actual_net_profit_usd > 0 ? "text-emerald" : "text-crimson"}`}>
            {formatUsd(payload?.actual_net_profit_usd, 4)}
          </span>
        </div>
      </div>
    </div>
  );
}

export default function ExecutionPanel({ cycle }) {
  return (
    <div data-testid="execution-panel" className="glass rounded-2xl p-5 h-full">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-emerald">
            <SendHorizonal className="w-3.5 h-3.5" />
            <span>04 · Submission</span>
          </div>
          <h3 className="text-lg font-semibold text-white/95 mt-1">
            Titan builder · bundle inclusion
          </h3>
        </div>
        {cycle && (
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-[0.18em] text-white/40">Cycle PnL</div>
            <div
              className={`numeric text-xl font-semibold ${
                cycle.total_net_profit_usd > 0 ? "text-emerald" : "text-crimson"
              }`}
            >
              {formatUsd(cycle.total_net_profit_usd, 4)}
            </div>
          </div>
        )}
      </div>

      {!cycle ? (
        <div className="text-white/30 text-[12px] py-12 text-center">
          Submission idle · run pipeline to dispatch bundle
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <BundleCard phase="C1 · Block N" payload={cycle.c1} tone="cyan" />
          <BundleCard phase="C2 · Block N+1" payload={cycle.c2} tone="violet" />
        </div>
      )}
    </div>
  );
}
