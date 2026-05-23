import React from "react";
import { motion } from "framer-motion";
import { Activity, RefreshCcw, Pause, Play, Hexagon } from "lucide-react";
import { formatUsd } from "../lib/api";

function Stat({ label, value, accent }) {
  return (
    <div className="flex flex-col items-end">
      <span className="text-[10px] uppercase tracking-[0.18em] text-white/40 font-medium">
        {label}
      </span>
      <span className={`numeric text-sm font-semibold ${accent || "text-white/95"}`}>
        {value}
      </span>
    </div>
  );
}

export default function HeaderBar({
  status,
  telemetry,
  autoScan,
  onToggleAutoScan,
  onRefresh,
}) {
  return (
    <header
      data-testid="header-bar"
      className="sticky top-0 z-40 backdrop-blur-xl border-b border-white/[0.06] bg-obsidian-950/70"
    >
      <div className="mx-auto max-w-[1680px] px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <motion.div
            initial={{ scale: 0.85, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            className="relative w-10 h-10 rounded-xl flex items-center justify-center
                       bg-gradient-to-br from-plasma-400/30 via-violet-500/25 to-emerald/20
                       border border-white/10 shadow-[0_0_30px_-8px_rgba(0,190,236,0.45)]"
          >
            <Hexagon className="w-5 h-5 text-plasma-200" strokeWidth={1.5} />
            <span className="absolute -bottom-1 -right-1 w-2.5 h-2.5 rounded-full bg-emerald shadow-[0_0_8px_rgba(31,224,143,0.7)]" />
          </motion.div>
          <div className="leading-tight">
            <div className="flex items-baseline gap-2">
              <h1
                className="font-display text-2xl tracking-tight text-white"
                style={{ fontStyle: "italic" }}
              >
                Apex
              </h1>
              <span className="font-display text-2xl tracking-tight text-gradient-cyan">
                Omega
              </span>
              <span className="text-[10px] uppercase tracking-[0.3em] text-white/30 ml-1">
                Final 2.0
              </span>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-white/50 numeric">
              <span className="live-dot" />
              <span>POLYGON · 137</span>
              <span className="text-white/20">·</span>
              <span>BLOCK {status?.block?.toLocaleString() ?? "—"}</span>
              <span className="text-white/20">·</span>
              <span className="uppercase text-plasma-200/90">{status?.mode ?? "online"}</span>
            </div>
          </div>
        </div>

        <div className="hidden md:flex items-center gap-7">
          <Stat
            label="Cycles"
            value={telemetry?.cycles_total ?? 0}
          />
          <Stat
            label="C1 PnL"
            value={formatUsd(telemetry?.c1_profit ?? 0, 4)}
            accent={(telemetry?.c1_profit ?? 0) >= 0 ? "text-emerald" : "text-crimson"}
          />
          <Stat
            label="C2 PnL"
            value={formatUsd(telemetry?.c2_profit ?? 0, 4)}
            accent={(telemetry?.c2_profit ?? 0) >= 0 ? "text-emerald" : "text-crimson"}
          />
          <Stat
            label="Total Profit"
            value={formatUsd(telemetry?.total_profit ?? 0, 4)}
            accent={(telemetry?.total_profit ?? 0) >= 0 ? "text-emerald" : "text-crimson"}
          />
        </div>

        <div className="flex items-center gap-2">
          <button
            data-testid="toggle-autoscan-btn"
            onClick={onToggleAutoScan}
            className={`group flex items-center gap-2 px-3 py-1.5 rounded-full border text-[12px] font-medium tracking-wide transition-all
              ${autoScan
                ? "bg-emerald/10 border-emerald/30 text-emerald"
                : "bg-white/[0.03] border-white/10 text-white/60"}`}
          >
            {autoScan ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}
            {autoScan ? "Live · Pause" : "Paused · Resume"}
          </button>
          <button
            data-testid="refresh-btn"
            onClick={onRefresh}
            className="p-2 rounded-full bg-white/[0.03] border border-white/10 hover:bg-white/[0.06] transition"
            title="Refresh"
          >
            <RefreshCcw className="w-3.5 h-3.5 text-white/70" />
          </button>
        </div>
      </div>
    </header>
  );
}
