import React from "react";
import { Database } from "lucide-react";

const FAMILY_COLOR = {
  v2: "#6DE7FF",
  v3: "#33D5FF",
  algebra: "#A78BFA",
  balancer: "#FFB020",
  curve: "#1FE08F",
};

export default function GraphPanel({ graph }) {
  const pools = graph?.pools || [];
  // Group by pair
  const byPair = React.useMemo(() => {
    const m = {};
    pools.forEach((p) => {
      const key = `${p.base}/${p.quote}`;
      (m[key] = m[key] || []).push(p);
    });
    return m;
  }, [pools]);

  return (
    <div data-testid="graph-panel" className="glass rounded-2xl p-5 h-full">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-white/40">
            <Database className="w-3.5 h-3.5" />
            <span>Liquidity Graph</span>
          </div>
          <h3 className="text-lg font-semibold text-white/95 mt-1">
            Polygon 137 · pool universe ({pools.length})
          </h3>
        </div>
        <span className="text-[11px] text-white/40 numeric">blk #{graph?.block?.toLocaleString() ?? "—"}</span>
      </div>

      <div className="max-h-[300px] overflow-y-auto pr-1 space-y-3">
        {Object.entries(byPair).map(([pair, pls]) => {
          const min = Math.min(...pls.map((p) => p.usd_per_base));
          const max = Math.max(...pls.map((p) => p.usd_per_base));
          const spread = ((max - min) / min) * 10_000;
          return (
            <div key={pair} className="border-l-2 border-white/[0.06] pl-3">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-white/90 text-[12.5px]">{pair}</span>
                <div className="flex items-center gap-3 text-[10.5px]">
                  <span className="text-white/45 numeric">${min.toFixed(5)} – ${max.toFixed(5)}</span>
                  <span
                    className={`numeric font-semibold ${
                      spread > 10 ? "text-emerald" : spread > 4 ? "text-amber" : "text-white/50"
                    }`}
                  >
                    {spread.toFixed(1)} bps
                  </span>
                </div>
              </div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {pls.map((p) => {
                  const norm = (p.usd_per_base - min) / Math.max(max - min, 1e-9);
                  return (
                    <span
                      key={p.pool_id}
                      title={`${p.venue} · ${p.family} · $${p.usd_per_base.toFixed(6)} · TVL $${p.tvl_usd.toLocaleString()}`}
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border"
                      style={{
                        background: `rgba(255,255,255,0.025)`,
                        borderColor: FAMILY_COLOR[p.family] + "55",
                        color: FAMILY_COLOR[p.family],
                      }}
                    >
                      <span className="w-1 h-1 rounded-full" style={{ background: FAMILY_COLOR[p.family] }} />
                      {p.venue}
                      <span className="text-white/35 ml-1 numeric">
                        {norm < 0.05 ? "buy" : norm > 0.95 ? "sell" : ""}
                      </span>
                    </span>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
