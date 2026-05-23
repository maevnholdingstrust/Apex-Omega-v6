import React from "react";
import { ShieldAlert } from "lucide-react";
import { fetchRisk } from "../lib/api";

const ROWS = [
  { key: "route_fragility", label: "Route fragility", inverse: true },
  { key: "state_divergence", label: "State divergence", inverse: true },
  { key: "revert_probability", label: "Revert probability", inverse: true },
  { key: "mempool_toxicity", label: "Mempool toxicity", inverse: true },
  { key: "liquidity_saturation", label: "Liquidity saturation", inverse: true },
  { key: "v3_tick_confidence", label: "V3 tick confidence", inverse: false },
];

function Bar({ value, inverse }) {
  const pct = Math.max(0, Math.min(1, value));
  // For inverse (where higher = worse), color: green->amber->red
  // For non-inverse (higher = better), color: red->amber->green
  const ratio = inverse ? pct : 1 - pct;
  const hue = 140 - ratio * 140; // 140 (green) to 0 (red)
  const color = `hsl(${hue}, 80%, 55%)`;
  return (
    <div className="relative w-full h-1.5 rounded-full bg-white/[0.05] overflow-hidden">
      <div
        className="absolute inset-y-0 left-0 rounded-full"
        style={{
          width: `${pct * 100}%`,
          background: `linear-gradient(90deg, ${color}55, ${color})`,
          boxShadow: `0 0 12px ${color}66`,
        }}
      />
    </div>
  );
}

export default function RiskPanel({ opp }) {
  const [risk, setRisk] = React.useState(null);

  React.useEffect(() => {
    if (!opp?.opp_id) {
      setRisk(null);
      return;
    }
    fetchRisk(opp.opp_id).then(setRisk).catch(() => setRisk(null));
  }, [opp?.opp_id]);

  return (
    <div data-testid="risk-panel" className="glass rounded-2xl p-5 h-full">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-white/40">
            <ShieldAlert className="w-3.5 h-3.5" />
            <span>Risk Surface</span>
          </div>
          <h3 className="text-lg font-semibold text-white/95 mt-1">
            Institutional validation gates
          </h3>
        </div>
        {risk && (
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-[0.18em] text-white/40">Overall</div>
            <div
              className={`numeric text-xl font-semibold ${
                risk.overall_risk_score < 0.35
                  ? "text-emerald"
                  : risk.overall_risk_score < 0.6
                  ? "text-amber"
                  : "text-crimson"
              }`}
            >
              {(risk.overall_risk_score * 100).toFixed(1)}
            </div>
          </div>
        )}
      </div>

      {!risk ? (
        <div className="text-white/30 text-[12px] py-8 text-center">
          Awaiting opportunity selection…
        </div>
      ) : (
        <div className="space-y-3">
          {ROWS.map((r) => (
            <div key={r.key}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[11.5px] text-white/65">{r.label}</span>
                <span className="numeric text-[11.5px] text-white/85">
                  {(risk[r.key] * 100).toFixed(1)}%
                </span>
              </div>
              <Bar value={risk[r.key]} inverse={r.inverse} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
