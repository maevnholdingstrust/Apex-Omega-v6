import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Toaster, toast } from "sonner";
import {
  Activity,
  Zap,
  Crosshair,
  Send,
  ChevronRight,
  Cpu,
  Radio,
  Sparkles,
  Pause,
  Play,
  RefreshCcw,
} from "lucide-react";
import {
  fetchStatus,
  fetchTelemetry,
  fetchOpportunities,
  triggerScan,
  runPipeline,
  fetchCycles,
  fetchGraph,
  formatUsd,
  formatBps,
  shortHash,
} from "./lib/api";
import HeaderBar from "./components/HeaderBar";
import PipelineRibbon from "./components/PipelineRibbon";
import OpportunityMatrix from "./components/OpportunityMatrix";
import OpportunityDetail from "./components/OpportunityDetail";
import C1Panel from "./components/C1Panel";
import C2Panel from "./components/C2Panel";
import RiskPanel from "./components/RiskPanel";
import ExecutionPanel from "./components/ExecutionPanel";
import ArchivePanel from "./components/ArchivePanel";
import EvCurveChart from "./components/EvCurveChart";
import GraphPanel from "./components/GraphPanel";

export default function App() {
  const [status, setStatus] = React.useState(null);
  const [telemetry, setTelemetry] = React.useState(null);
  const [opportunities, setOpportunities] = React.useState([]);
  const [cycles, setCycles] = React.useState([]);
  const [selectedOpp, setSelectedOpp] = React.useState(null);
  const [activeCycle, setActiveCycle] = React.useState(null);
  const [stage, setStage] = React.useState("discovery"); // discovery | execution | submission | archive
  const [autoScan, setAutoScan] = React.useState(true);
  const [tradeSize, setTradeSize] = React.useState(12000);
  const [minSpreadBps, setMinSpreadBps] = React.useState(8);
  const [graph, setGraph] = React.useState(null);
  const [scanning, setScanning] = React.useState(false);
  const [executing, setExecuting] = React.useState(false);

  const refreshAll = React.useCallback(async () => {
    try {
      const [s, t, c, g] = await Promise.all([
        fetchStatus(),
        fetchTelemetry(),
        fetchCycles(40),
        fetchGraph(),
      ]);
      setStatus(s);
      setTelemetry(t);
      setCycles(c.cycles || []);
      setGraph(g);
    } catch (e) {
      console.error(e);
    }
  }, []);

  const doScan = React.useCallback(async () => {
    setScanning(true);
    try {
      const r = await triggerScan(tradeSize, minSpreadBps);
      setOpportunities(r.opportunities || []);
      if (r.opportunities?.length && !selectedOpp) {
        setSelectedOpp(r.opportunities[0]);
      } else if (selectedOpp && r.opportunities?.length) {
        // refresh selection if same pair still exists
        const same = r.opportunities.find((o) => o.pair === selectedOpp.pair);
        if (same) setSelectedOpp(same);
      }
    } catch (e) {
      toast.error("Scan failed: " + e.message);
    } finally {
      setScanning(false);
    }
  }, [tradeSize, minSpreadBps, selectedOpp]);

  React.useEffect(() => {
    refreshAll();
    fetchOpportunities(30).then((d) => {
      setOpportunities(d.opportunities || []);
      if (d.opportunities?.length) setSelectedOpp(d.opportunities[0]);
    });
    doScan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    const id = setInterval(refreshAll, 4000);
    return () => clearInterval(id);
  }, [refreshAll]);

  React.useEffect(() => {
    if (!autoScan) return;
    const id = setInterval(doScan, 5000);
    return () => clearInterval(id);
  }, [autoScan, doScan]);

  const executeFullPipeline = async () => {
    if (!selectedOpp) {
      toast.error("Select an opportunity first");
      return;
    }
    setExecuting(true);
    setStage("execution");
    try {
      const cycle = await runPipeline(selectedOpp.opp_id);
      setActiveCycle(cycle);
      // small delay then move to submission
      setTimeout(() => setStage("submission"), 900);
      setTimeout(() => {
        setStage("archive");
        refreshAll();
        toast.success(
          `Cycle complete · Net ${formatUsd(cycle.total_net_profit_usd, 4)}`,
        );
      }, 1900);
    } catch (e) {
      toast.error("Pipeline failed: " + (e.response?.data?.detail || e.message));
      setStage("discovery");
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="relative min-h-screen z-10">
      <Toaster
        position="bottom-right"
        theme="dark"
        toastOptions={{
          style: {
            background: "rgba(10,12,19,0.92)",
            border: "1px solid rgba(255,255,255,0.08)",
            backdropFilter: "blur(14px)",
            color: "#E4E7F0",
          },
        }}
      />

      <HeaderBar
        status={status}
        telemetry={telemetry}
        autoScan={autoScan}
        onToggleAutoScan={() => setAutoScan((s) => !s)}
        onRefresh={refreshAll}
      />

      <PipelineRibbon stage={stage} activeCycle={activeCycle} />

      <main className="relative mx-auto max-w-[1680px] px-6 pb-24">
        {/* Top row: discovery controls + opportunity matrix + selected opp detail */}
        <section className="grid grid-cols-12 gap-5 mt-6">
          <div className="col-span-12 xl:col-span-8" data-testid="discovery-section">
            <OpportunityMatrix
              opportunities={opportunities}
              selected={selectedOpp}
              onSelect={setSelectedOpp}
              tradeSize={tradeSize}
              setTradeSize={setTradeSize}
              minSpreadBps={minSpreadBps}
              setMinSpreadBps={setMinSpreadBps}
              scanning={scanning}
              onScan={doScan}
              autoScan={autoScan}
              onToggleAutoScan={() => setAutoScan((s) => !s)}
            />
          </div>
          <div className="col-span-12 xl:col-span-4">
            <OpportunityDetail
              opp={selectedOpp}
              onExecute={executeFullPipeline}
              executing={executing}
            />
          </div>
        </section>

        {/* EV curve + Risk panel */}
        <section className="grid grid-cols-12 gap-5 mt-5">
          <div className="col-span-12 lg:col-span-7">
            <EvCurveChart opp={selectedOpp} />
          </div>
          <div className="col-span-12 lg:col-span-5">
            <RiskPanel opp={selectedOpp} />
          </div>
        </section>

        {/* C1 + C2 panels */}
        <section className="grid grid-cols-12 gap-5 mt-5">
          <div className="col-span-12 lg:col-span-6">
            <C1Panel cycle={activeCycle} />
          </div>
          <div className="col-span-12 lg:col-span-6">
            <C2Panel cycle={activeCycle} />
          </div>
        </section>

        {/* Execution + Liquidity graph */}
        <section className="grid grid-cols-12 gap-5 mt-5">
          <div className="col-span-12 lg:col-span-5">
            <ExecutionPanel cycle={activeCycle} />
          </div>
          <div className="col-span-12 lg:col-span-7">
            <GraphPanel graph={graph} />
          </div>
        </section>

        {/* Archive */}
        <section className="mt-5">
          <ArchivePanel
            cycles={cycles}
            onSelect={(c) => {
              setActiveCycle(c);
              setStage("archive");
            }}
          />
        </section>

        <footer className="text-[11px] tracking-widest uppercase text-white/30 mt-12 pb-6 numeric flex items-center justify-between">
          <span>Apex Omega Final 2.0 · Polygon 137 · Simulated Institutional Architecture</span>
          <span>blk #{status?.block?.toLocaleString() ?? "—"}</span>
        </footer>
      </main>
    </div>
  );
}
