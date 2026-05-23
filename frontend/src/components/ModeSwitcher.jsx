import React from "react";
import { motion } from "framer-motion";
import { Zap, EyeOff, FlaskConical, Server, RefreshCcw } from "lucide-react";
import { api, shortHash } from "../lib/api";
import { toast } from "sonner";

const MODES = [
  { id: "LIVE",   label: "LIVE",   icon: Zap,           tone: "from-emerald/30 to-plasma-400/25 border-emerald/45 text-emerald",   desc: "fork-sim → real Titan broadcast" },
  { id: "SHADOW", label: "SHADOW", icon: EyeOff,        tone: "from-amber/30 to-ember/25 border-amber/45 text-amber",              desc: "fork-sim only · no broadcast" },
  { id: "SIM",    label: "SIM",    icon: FlaskConical,  tone: "from-violet-500/30 to-plasma-400/20 border-violet-500/45 text-violet-300", desc: "pure in-memory simulation" },
];

export default function ModeSwitcher({ status, onModeChanged }) {
  const currentMode = status?.mode || "LIVE";
  const fork = status?.fork || {};
  const [pending, setPending] = React.useState(null);
  const [respawning, setRespawning] = React.useState(false);

  const setMode = async (mode) => {
    setPending(mode);
    try {
      const r = await api.post("/mode", { mode });
      toast.success(`Mode switched to ${r.data.mode}`);
      onModeChanged?.();
    } catch (e) {
      toast.error("Mode switch failed: " + (e.response?.data?.detail || e.message));
    } finally {
      setPending(null);
    }
  };

  const respawn = async () => {
    setRespawning(true);
    try {
      const r = await api.post("/fork/respawn");
      toast.success(`Anvil fork respawned at block #${r.data.current_block}`);
      onModeChanged?.();
    } catch (e) {
      toast.error("Respawn failed: " + e.message);
    } finally {
      setRespawning(false);
    }
  };

  return (
    <div
      data-testid="mode-switcher"
      className="mx-auto max-w-[1680px] px-6 mt-4 flex items-center gap-3 flex-wrap"
    >
      <div className="inline-flex p-0.5 rounded-full glass-strong border border-white/[0.08]">
        {MODES.map((m) => {
          const isActive = m.id === currentMode;
          const Icon = m.icon;
          return (
            <button
              key={m.id}
              data-testid={`mode-btn-${m.id}`}
              onClick={() => !isActive && setMode(m.id)}
              disabled={pending === m.id}
              className={`group relative px-3.5 py-1.5 rounded-full text-[11px] font-semibold tracking-wide flex items-center gap-1.5 transition-all
                ${isActive
                  ? `bg-gradient-to-r ${m.tone} border`
                  : "text-white/45 hover:text-white/85 border border-transparent"}`}
              title={m.desc}
            >
              <Icon className="w-3 h-3" />
              {pending === m.id ? "…" : m.label}
              {isActive && <span className="live-dot ml-1" />}
            </button>
          );
        })}
      </div>

      {/* Fork health pill */}
      <div
        data-testid="fork-health-pill"
        className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-[11px]
          ${fork.ok
            ? "bg-emerald/[0.08] border-emerald/30 text-emerald"
            : "bg-crimson/[0.08] border-crimson/30 text-crimson"}`}
      >
        <Server className="w-3 h-3" />
        <span className="font-semibold tracking-wide uppercase">
          {fork.ok ? "Anvil Fork" : "Fork Down"}
        </span>
        {fork.ok && (
          <>
            <span className="text-white/35">·</span>
            <span className="numeric text-white/75">
              blk #{fork.current_block?.toLocaleString() ?? "—"}
            </span>
            <span className="text-white/35">·</span>
            <span className="numeric text-white/55">
              uptime {fork.uptime_s != null ? `${Math.round(fork.uptime_s)}s` : "—"}
            </span>
          </>
        )}
        <button
          data-testid="fork-respawn-btn"
          onClick={respawn}
          disabled={respawning}
          className="ml-1 p-1 rounded-full hover:bg-white/[0.06] transition"
          title="Respawn Anvil at current head"
        >
          <RefreshCcw className={`w-3 h-3 ${respawning ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* Active gate label */}
      <span className="text-[10.5px] uppercase tracking-[0.22em] text-white/40 ml-auto">
        {currentMode === "SIM"
          ? "Fork-sim gate BYPASSED · pure simulation"
          : status?.require_fork_sim_before_submit
          ? "Fork-sim gate REQUIRED before every submit"
          : "Fork-sim gate optional"}
      </span>
    </div>
  );
}
