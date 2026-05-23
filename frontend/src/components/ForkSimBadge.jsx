import React from "react";
import { Server, ShieldCheck, ShieldX, Pause } from "lucide-react";

export default function ForkSimBadge({ fork }) {
  if (!fork) return null;
  if (fork.skipped) {
    return (
      <div
        data-testid="fork-sim-badge"
        className="mt-3 px-3 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] flex items-center gap-2 text-[11px]"
      >
        <Pause className="w-3 h-3 text-white/40" />
        <span className="text-white/55 uppercase tracking-[0.18em]">Fork sim skipped</span>
        <span className="text-white/35">·</span>
        <span className="text-white/55">{fork.reason || "bypassed"}</span>
      </div>
    );
  }
  const pass = !!fork.pass;
  return (
    <div
      data-testid="fork-sim-badge"
      className={`mt-3 px-3 py-2 rounded-lg border flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]
        ${pass
          ? "border-emerald/30 bg-emerald/[0.06]"
          : "border-crimson/30 bg-crimson/[0.06]"}`}
    >
      <span className="flex items-center gap-1.5">
        {pass ? (
          <ShieldCheck className="w-3.5 h-3.5 text-emerald" />
        ) : (
          <ShieldX className="w-3.5 h-3.5 text-crimson" />
        )}
        <span
          className={`uppercase tracking-[0.18em] font-semibold ${
            pass ? "text-emerald" : "text-crimson"
          }`}
        >
          Anvil fork-sim {pass ? "PASS" : "FAIL"}
        </span>
      </span>
      {fork.fork_block && (
        <span className="text-white/55 numeric">
          <Server className="w-3 h-3 inline mr-1 -mt-0.5 text-white/35" />
          blk #{Number(fork.fork_block).toLocaleString()}
        </span>
      )}
      <span className="text-white/55 numeric">
        steps {fork.step_count ?? fork.steps?.length ?? 0}
      </span>
      {fork.total_gas_estimate != null && (
        <span className="text-white/55 numeric">
          gas ≈ {Number(fork.total_gas_estimate).toLocaleString()}
        </span>
      )}
      {fork.mode && (
        <span className="text-white/35 text-[10px] uppercase tracking-[0.18em] ml-auto">
          {fork.mode} mode
        </span>
      )}
      {!pass && fork.revert_reason && (
        <div className="basis-full text-crimson/85 text-[10.5px] font-mono pl-5 mt-0.5">
          ↳ {fork.revert_reason}
        </div>
      )}
    </div>
  );
}
