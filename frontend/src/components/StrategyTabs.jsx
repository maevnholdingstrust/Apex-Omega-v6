import React from "react";
import { motion } from "framer-motion";
import { Crosshair, Skull } from "lucide-react";

const STRATEGIES = [
  {
    id: "arbitrage",
    label: "Arbitrage",
    sub: "C1 Aggressor · C2 Surgeon · Merkle",
    icon: Crosshair,
    tone: "plasma",
  },
  {
    id: "liquidation",
    label: "Liquidation",
    sub: "Aave V3 · HF<1 · seize + bonus",
    icon: Skull,
    tone: "crimson",
  },
];

export default function StrategyTabs({ active, onChange, telemetry }) {
  return (
    <div data-testid="strategy-tabs" className="mx-auto max-w-[1680px] px-6 mt-5">
      <div className="grid grid-cols-2 gap-3">
        {STRATEGIES.map((s) => {
          const Icon = s.icon;
          const isActive = s.id === active;
          const metric =
            s.id === "arbitrage"
              ? {
                  label: "Cycles",
                  value: telemetry?.cycles_total ?? 0,
                  pnl: telemetry?.total_profit ?? 0,
                }
              : {
                  label: "Liquidations",
                  value: telemetry?.liquidations_executed ?? 0,
                  pnl: telemetry?.liquidation_bonus_total ?? 0,
                };
          const accent = s.tone === "plasma"
            ? "from-plasma-400/20 via-violet-500/15 to-transparent border-plasma-300/40"
            : "from-crimson/20 via-ember/15 to-transparent border-crimson/40";
          return (
            <button
              key={s.id}
              data-testid={`strategy-tab-${s.id}`}
              onClick={() => onChange(s.id)}
              className={`group relative rounded-2xl px-5 py-3.5 border text-left transition-all
                ${isActive
                  ? `bg-gradient-to-br ${accent}`
                  : "bg-white/[0.02] border-white/[0.05] hover:bg-white/[0.04]"}`}
            >
              {isActive && (
                <motion.div
                  layoutId="strategy-glow"
                  className="absolute -inset-px rounded-2xl pointer-events-none"
                  style={{
                    background: s.tone === "plasma"
                      ? "radial-gradient(circle at 50% 0%, rgba(0,190,236,0.18) 0%, transparent 70%)"
                      : "radial-gradient(circle at 50% 0%, rgba(255,61,90,0.18) 0%, transparent 70%)",
                  }}
                />
              )}
              <div className="flex items-center justify-between relative">
                <div className="flex items-center gap-3">
                  <div
                    className={`w-10 h-10 rounded-xl flex items-center justify-center border
                      ${isActive
                        ? s.tone === "plasma"
                          ? "bg-plasma-400/15 border-plasma-300/40 text-plasma-200"
                          : "bg-crimson/15 border-crimson/40 text-crimson"
                        : "bg-white/[0.03] border-white/10 text-white/40"}`}
                  >
                    <Icon className="w-4 h-4" strokeWidth={1.6} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-[11px] uppercase tracking-[0.24em] font-semibold ${
                          isActive
                            ? s.tone === "plasma"
                              ? "text-plasma-200"
                              : "text-crimson"
                            : "text-white/45"
                        }`}
                      >
                        {s.label}
                      </span>
                      {isActive && <span className="live-dot" />}
                    </div>
                    <div className="text-[11.5px] text-white/55 mt-0.5">{s.sub}</div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] uppercase tracking-[0.18em] text-white/40">
                    {metric.label}
                  </div>
                  <div className="numeric text-base font-semibold text-white/90">
                    {metric.value}
                  </div>
                  <div
                    className={`numeric text-[11px] font-medium ${
                      metric.pnl >= 0 ? "text-emerald" : "text-crimson"
                    }`}
                  >
                    {metric.pnl >= 0 ? "+" : ""}${metric.pnl.toFixed(2)}
                  </div>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
