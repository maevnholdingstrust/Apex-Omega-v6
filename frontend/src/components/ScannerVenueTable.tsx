import React, { useEffect, useMemo, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Types — mirrors python/apex_omega_core/core/types.py :: VenueQuoteRow
// ---------------------------------------------------------------------------

export type ScannerVenueRow = {
  token_address: string;
  token_symbol: string;
  venue: string;
  pool_address: string;
  buy_price_executable: number;
  sell_price_executable: number;
  liquidity_usd?: number;
  fee_bps?: number;
  freshness_ms?: number;
  quote_confidence?: string;
  block_number?: number | null;
  source?: string;
  updated_at_ms?: number;
  metadata?: Record<string, unknown>;
};

type SortKey = keyof ScannerVenueRow;
type SortDir = "asc" | "desc";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Spread in basis points between best-sell and best-buy for the same token. */
function spreadBps(buy: number, sell: number): number {
  if (buy <= 0) return 0;
  return Math.round(((sell - buy) / buy) * 10_000);
}

/** Age of a quote in milliseconds from now. */
function ageMs(updatedAtMs?: number): number | null {
  if (updatedAtMs == null) return null;
  return Date.now() - updatedAtMs;
}

/** Format age for display: "<1s", "3s", "1m 2s", etc. */
function fmtAge(ms: number): string {
  if (ms < 1_000) return "<1s";
  const s = Math.floor(ms / 1_000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function confidenceClass(conf?: string): string {
  switch (conf) {
    case "high":
      return "badge-high";
    case "medium":
      return "badge-medium";
    case "low":
      return "badge-low";
    default:
      return "badge-unknown";
  }
}

// ---------------------------------------------------------------------------
// Per-token best-buy / best-sell index built from all rows
// ---------------------------------------------------------------------------

type TokenExtrema = {
  bestBuyPool: string | null;
  bestSellPool: string | null;
};

function buildExtrema(rows: ScannerVenueRow[]): Map<string, TokenExtrema> {
  // Pre-build a price lookup map for O(1) access inside the main loop.
  const priceByPool = new Map<string, { buy: number; sell: number }>();
  for (const row of rows) {
    priceByPool.set(row.pool_address, {
      buy: row.buy_price_executable,
      sell: row.sell_price_executable,
    });
  }

  const map = new Map<string, TokenExtrema>();
  for (const row of rows) {
    if (row.quote_confidence !== "high") continue;
    const ex = map.get(row.token_address) ?? {
      bestBuyPool: null,
      bestSellPool: null,
    };
    // Best buy = lowest buy price (cheapest to acquire)
    const currentBuyPrice =
      ex.bestBuyPool != null ? priceByPool.get(ex.bestBuyPool)?.buy : null;
    if (currentBuyPrice == null || row.buy_price_executable < currentBuyPrice) {
      ex.bestBuyPool = row.pool_address;
    }
    // Best sell = highest sell price (most revenue when selling)
    const currentSellPrice =
      ex.bestSellPool != null ? priceByPool.get(ex.bestSellPool)?.sell : null;
    if (currentSellPrice == null || row.sell_price_executable > currentSellPrice) {
      ex.bestSellPool = row.pool_address;
    }
    map.set(row.token_address, ex);
  }
  return map;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StalenessIndicator({ updatedAtMs, freshnessMsThreshold = 5_000 }: {
  updatedAtMs?: number;
  freshnessMsThreshold?: number;
}) {
  const [now, setNow] = useState(Date.now());
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    timerRef.current = setInterval(() => setNow(Date.now()), 1_000);
    return () => {
      if (timerRef.current != null) clearInterval(timerRef.current);
    };
  }, []);

  const ms = updatedAtMs != null ? now - updatedAtMs : null;
  if (ms == null) return <span className="staleness staleness-unknown">—</span>;

  const stale = ms > freshnessMsThreshold;
  return (
    <span className={`staleness ${stale ? "staleness-stale" : "staleness-fresh"}`}>
      {fmtAge(ms)}
    </span>
  );
}

function SortIndicator({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <span className="sort-icon sort-icon-inactive">⇅</span>;
  return (
    <span className="sort-icon sort-icon-active">
      {dir === "asc" ? "↑" : "↓"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export type ScannerVenueTableProps = {
  rows: ScannerVenueRow[];
  /** Rows older than this (ms) are highlighted as stale. Default: 5 000 ms. */
  stalenessThresholdMs?: number;
  /** Called when the user clicks a row. */
  onRowClick?: (row: ScannerVenueRow) => void;
};

export const ScannerVenueTable: React.FC<ScannerVenueTableProps> = ({
  rows,
  stalenessThresholdMs = 5_000,
  onRowClick,
}) => {
  const [filterToken, setFilterToken] = useState("");
  const [filterVenue, setFilterVenue] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("token_symbol");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // Build per-token extrema once per rows snapshot
  const extrema = useMemo(() => buildExtrema(rows), [rows]);

  const filtered = useMemo(() => {
    const tokenQ = filterToken.trim().toLowerCase();
    const venueQ = filterVenue.trim().toLowerCase();
    return rows.filter((r) => {
      if (tokenQ && !r.token_symbol.toLowerCase().includes(tokenQ)) return false;
      if (venueQ && !r.venue.toLowerCase().includes(venueQ)) return false;
      return true;
    });
  }, [rows, filterToken, filterVenue]);

  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      let cmp = 0;
      if (typeof av === "number" && typeof bv === "number") {
        cmp = av - bv;
      } else {
        cmp = String(av).localeCompare(String(bv));
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [filtered, sortKey, sortDir]);

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function Th({
    label,
    col,
    title,
  }: {
    label: string;
    col: SortKey;
    title?: string;
  }) {
    return (
      <th
        className="scanner-th"
        title={title}
        onClick={() => handleSort(col)}
        aria-sort={
          sortKey === col
            ? sortDir === "asc"
              ? "ascending"
              : "descending"
            : "none"
        }
      >
        {label}
        <SortIndicator active={sortKey === col} dir={sortDir} />
      </th>
    );
  }

  return (
    <div className="scanner-venue-table-wrapper">
      {/* ── Filters ── */}
      <div className="scanner-filters">
        <label className="scanner-filter-label">
          Token
          <input
            className="scanner-filter-input"
            type="text"
            placeholder="e.g. WETH"
            value={filterToken}
            onChange={(e) => setFilterToken(e.target.value)}
          />
        </label>
        <label className="scanner-filter-label">
          Venue
          <input
            className="scanner-filter-input"
            type="text"
            placeholder="e.g. Uniswap"
            value={filterVenue}
            onChange={(e) => setFilterVenue(e.target.value)}
          />
        </label>
        <span className="scanner-row-count">
          {sorted.length} / {rows.length} rows
        </span>
      </div>

      {/* ── Table ── */}
      <div className="scanner-table-scroll">
        <table className="scanner-table" aria-label="Scanner venue quotes">
          <thead>
            <tr>
              <Th label="Token" col="token_symbol" title="Token symbol" />
              <Th label="Venue" col="venue" title="Trading venue / DEX" />
              <Th
                label="Buy Price"
                col="buy_price_executable"
                title="Executable buy price (token units)"
              />
              <Th
                label="Sell Price"
                col="sell_price_executable"
                title="Executable sell price (token units)"
              />
              <th className="scanner-th" title="Spread in basis points (sell − buy)">
                Spread (bps)
              </th>
              <Th
                label="Liquidity"
                col="liquidity_usd"
                title="Pool liquidity in USD"
              />
              <Th label="Fee (bps)" col="fee_bps" title="Pool fee in basis points" />
              <Th
                label="Confidence"
                col="quote_confidence"
                title="Quote confidence level"
              />
              <th className="scanner-th" title="Time since last update">
                Age
              </th>
              <Th label="Block" col="block_number" title="Evaluation block number" />
              <Th label="Source" col="source" title="Data source identifier" />
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td className="scanner-empty" colSpan={11}>
                  No rows match the current filters.
                </td>
              </tr>
            ) : (
              sorted.map((row) => {
                const ex = extrema.get(row.token_address);
                const isBestBuy = ex?.bestBuyPool === row.pool_address;
                const isBestSell = ex?.bestSellPool === row.pool_address;
                const age = ageMs(row.updated_at_ms);
                const stale =
                  age != null && age > stalenessThresholdMs;
                const bps = spreadBps(
                  row.buy_price_executable,
                  row.sell_price_executable
                );

                const rowClass = [
                  "scanner-row",
                  stale ? "scanner-row-stale" : "",
                  isBestBuy && isBestSell ? "scanner-row-best-both" : "",
                  isBestBuy && !isBestSell ? "scanner-row-best-buy" : "",
                  !isBestBuy && isBestSell ? "scanner-row-best-sell" : "",
                ]
                  .filter(Boolean)
                  .join(" ");

                return (
                  <tr
                    key={`${row.token_address}-${row.pool_address}`}
                    className={rowClass}
                    onClick={() => onRowClick?.(row)}
                    tabIndex={onRowClick ? 0 : undefined}
                    onKeyDown={
                      onRowClick
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onRowClick(row);
                            }
                          }
                        : undefined
                    }
                  >
                    <td className="scanner-td scanner-td-symbol">
                      <span className="token-symbol">{row.token_symbol}</span>
                      {isBestBuy && (
                        <span className="badge badge-best-buy" title="Best buy venue">
                          BUY
                        </span>
                      )}
                      {isBestSell && (
                        <span className="badge badge-best-sell" title="Best sell venue">
                          SELL
                        </span>
                      )}
                    </td>
                    <td className="scanner-td">{row.venue}</td>
                    <td className="scanner-td scanner-td-num">
                      {row.buy_price_executable.toFixed(6)}
                    </td>
                    <td className="scanner-td scanner-td-num">
                      {row.sell_price_executable.toFixed(6)}
                    </td>
                    <td
                      className={`scanner-td scanner-td-num ${
                        bps > 0 ? "spread-positive" : "spread-zero"
                      }`}
                    >
                      {bps}
                    </td>
                    <td className="scanner-td scanner-td-num">
                      {row.liquidity_usd != null
                        ? `$${row.liquidity_usd.toLocaleString(undefined, {
                            maximumFractionDigits: 0,
                          })}`
                        : "—"}
                    </td>
                    <td className="scanner-td scanner-td-num">
                      {row.fee_bps ?? "—"}
                    </td>
                    <td className="scanner-td">
                      <span
                        className={`badge ${confidenceClass(row.quote_confidence)}`}
                      >
                        {row.quote_confidence ?? "unknown"}
                      </span>
                    </td>
                    <td className="scanner-td">
                      <StalenessIndicator
                        updatedAtMs={row.updated_at_ms}
                        freshnessMsThreshold={stalenessThresholdMs}
                      />
                    </td>
                    <td className="scanner-td scanner-td-num">
                      {row.block_number ?? "—"}
                    </td>
                    <td className="scanner-td">{row.source ?? "—"}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ScannerVenueTable;
