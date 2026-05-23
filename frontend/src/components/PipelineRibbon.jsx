import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Radar, Crosshair, SendHorizonal, Archive } from "lucide-react";

const STAGES = [
  { id: "discovery", label: "Discovery", desc: "Liquidity gate · USD-norm · spread", icon: Radar },
  { id: "execution", label: "Execution", desc: "C1 TX (Block N) → C2 TX (Block N+1, same pair)", icon: Crosshair },
  { id: "submission", label: "Submission", desc: "2 Titan bundles · independent envelopes", icon: SendHorizonal },
  { id: "archive", label: "Archive", desc: "C1 + C2 reconciled · 1 cycle record", icon: Archive },
];

export default function PipelineRibbon({ stage = "discovery", activeCycle }) {
  const activeIdx = STAGES.findIndex((s) => s.id === stage);

  return (
    <div className="mx-auto max-w-[1680px] px-6 pt-5" data-testid="pipeline-ribbon">
      <div className="relative gradient-border p-[1px]">
        <div className="relative rounded-[18px] glass-strong p-4">
          {/* shimmer line */}
          <div className="absolute top-0 left-0 right-0 h-px pipeline-line opacity-60" />
          <div className="grid grid-cols-4 gap-3">
            {STAGES.map((s, idx) => {
              const active = idx === activeIdx;
              const done = idx < activeIdx;
              const Icon = s.icon;
              return (
                <div
                  key={s.id}
                  data-testid={`pipeline-stage-${s.id}`}
                  className={`relative group rounded-2xl px-4 py-3 border transition-all duration-300
                    ${active ? "bg-gradient-to-br from-plasma-400/15 via-violet-500/10 to-transparent border-plasma-300/40" :
                      done ? "bg-emerald/[0.05] border-emerald/20" : "bg-white/[0.015] border-white/[0.05]"}`}
                >
                  {/* progress glow */}
                  {active && (
                    <motion.div
                      layoutId="pipeline-glow"
                      className="absolute -inset-px rounded-2xl pointer-events-none"
                      style={{
                        background:
                          "radial-gradient(circle at 50% 0%, rgba(0,190,236,0.20) 0%, transparent 70%)",
                      }}
                    />
                  )}
                  <div className="flex items-center gap-3 relative">
                    <div
                      className={`w-9 h-9 rounded-xl flex items-center justify-center border
                        ${active ? "bg-plasma-400/15 border-plasma-300/40 text-plasma-200" :
                          done ? "bg-emerald/15 border-emerald/30 text-emerald" :
                          "bg-white/[0.03] border-white/10 text-white/40"}`}
                    >
                      <Icon className="w-4 h-4" strokeWidth={1.6} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between">
                        <span className={`text-[11px] uppercase tracking-[0.22em] font-semibold ${
                          active ? "text-plasma-200" : done ? "text-emerald" : "text-white/40"
                        }`}>
                          {String(idx + 1).padStart(2, "0")} · {s.label}
                        </span>
                        {active && <span className="live-dot" />}
                      </div>
                      <div className="text-[11.5px] text-white/55 mt-0.5 truncate">
                        {s.desc}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          {activeCycle && (
            <AnimatePresence>
              <motion.div
                key={activeCycle.cycle_id}
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="mt-3 flex items-center gap-4 text-[11px] text-white/55 numeric"
              >
                <span className="uppercase tracking-[0.22em] text-white/30">Active cycle</span>
                <span className="text-plasma-200/90">{activeCycle.cycle_id}</span>
                <span className="text-white/30">·</span>
                <span>{activeCycle.pair}</span>
                <span className="text-white/30">·</span>
                <span className={activeCycle.total_net_profit_usd > 0 ? "text-emerald" : "text-crimson"}>
                  Net ${activeCycle.total_net_profit_usd?.toFixed(4)}
                </span>
              </motion.div>
            </AnimatePresence>
          )}
        </div>
      </div>
    </div>
  );
}
