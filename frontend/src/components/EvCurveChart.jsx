import React from "react";
import { motion } from "framer-motion";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceDot,
  CartesianGrid,
} from "recharts";
import { TrendingUp } from "lucide-react";

export default function EvCurveChart({ opp }) {
  const curve = opp?.ev_curve || [];
  const selectedBuf = opp?.selected_buffer ?? 0;
  const selectedEv = opp?.selected_ev_usdc ?? 0;

  return (
    <div data-testid="ev-curve" className="glass rounded-2xl p-5 h-full">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-white/40">
            <TrendingUp className="w-3.5 h-3.5" />
            <span>EV Buffer Optimizer</span>
          </div>
          <h3 className="text-lg font-semibold text-white/95 mt-1">
            Hybrid buffer · expected value curve
          </h3>
        </div>
        {opp && (
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-[0.2em] text-white/40">Selected EV</div>
            <div className={`numeric text-xl font-semibold ${selectedEv > 0 ? "text-emerald" : "text-crimson"}`}>
              ${selectedEv.toFixed(4)}
            </div>
          </div>
        )}
      </div>

      <div className="h-[240px]">
        {curve.length === 0 ? (
          <div className="h-full flex items-center justify-center text-white/30 text-[12px]">
            Select an opportunity to render EV curve
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={curve.map((d) => ({
                buffer_pct: (d.buffer * 100).toFixed(3),
                buffer_raw: d.buffer,
                ev: d.ev,
                multiplier: d.multiplier,
              }))}
              margin={{ top: 12, right: 16, left: 0, bottom: 8 }}
            >
              <defs>
                <linearGradient id="evGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#33D5FF" stopOpacity={0.55} />
                  <stop offset="50%" stopColor="#7C3AED" stopOpacity={0.32} />
                  <stop offset="100%" stopColor="#7C3AED" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis
                dataKey="buffer_pct"
                tick={{ fontSize: 10, fill: "rgba(255,255,255,0.5)" }}
                tickLine={false}
                axisLine={{ stroke: "rgba(255,255,255,0.08)" }}
                label={{
                  value: "Buffer %",
                  position: "insideBottomRight",
                  offset: -2,
                  fontSize: 9,
                  fill: "rgba(255,255,255,0.35)",
                }}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "rgba(255,255,255,0.5)" }}
                tickLine={false}
                axisLine={{ stroke: "rgba(255,255,255,0.08)" }}
                tickFormatter={(v) => `$${v.toFixed(2)}`}
              />
              <Tooltip
                contentStyle={{
                  background: "rgba(10,12,19,0.95)",
                  border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: 8,
                  fontSize: 11,
                }}
                labelStyle={{ color: "rgba(255,255,255,0.6)" }}
                formatter={(value, name, props) => [
                  `$${Number(value).toFixed(4)}`,
                  `EV @ ${props.payload.multiplier.toFixed(2)}× base`,
                ]}
              />
              <Area
                type="monotone"
                dataKey="ev"
                stroke="#6DE7FF"
                strokeWidth={2}
                fill="url(#evGrad)"
                dot={{ r: 3, fill: "#33D5FF", stroke: "rgba(255,255,255,0.3)", strokeWidth: 1 }}
                activeDot={{ r: 5, fill: "#FFFFFF" }}
              />
              <ReferenceDot
                x={(selectedBuf * 100).toFixed(3)}
                y={selectedEv}
                r={6}
                fill="#1FE08F"
                stroke="white"
                strokeWidth={1.5}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
      {opp && (
        <div className="mt-2 grid grid-cols-6 gap-2 text-[10px] uppercase tracking-[0.16em] text-white/40">
          {curve.map((d) => (
            <div key={d.multiplier} className="text-center">
              <div className="text-white/30">{d.multiplier.toFixed(2)}×</div>
              <div
                className={`numeric mt-0.5 ${d.ev === selectedEv ? "text-emerald" : "text-white/70"}`}
              >
                ${d.ev.toFixed(2)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
