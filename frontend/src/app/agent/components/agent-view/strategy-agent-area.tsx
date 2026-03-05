import { GripHorizontal, Plus } from "lucide-react";
import { type FC, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useDeleteStrategy,
  useGetStrategyDetails,
  useGetStrategyHoldings,
  useGetStrategyList,
  useGetStrategyPortfolioSummary,
  useGetStrategyPriceCurve,
  useStopStrategy,
} from "@/api/strategy";
import CreateStrategyModal from "@/app/agent/components/strategy-items/modals/create-strategy-modal";
import { Button } from "@/components/ui/button";
import type { AgentViewProps } from "@/types/agent";
import type { Strategy } from "@/types/strategy";
import {
  PortfolioPositionsGroup,
  StrategyComposeList,
  TradeStrategyGroup,
} from "../strategy-items";

const EmptyIllustration = () => (
  <svg
    viewBox="0 0 258 185"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    className="h-[185px] w-[258px]"
  >
    <rect
      x="40"
      y="30"
      width="178"
      height="125"
      rx="8"
      className="fill-muted"
    />
    <rect x="60" y="60" width="138" height="8" rx="4" className="fill-border" />
    <rect x="60" y="80" width="100" height="8" rx="4" className="fill-border" />
    <rect
      x="60"
      y="100"
      width="120"
      height="8"
      rx="4"
      className="fill-border"
    />
  </svg>
);

type MobileTab = "strategies" | "history";

// Mobile portfolio panel height constraints (px)
const PORTFOLIO_DEFAULT_H = 440; // shows stats + chart on first open
const PORTFOLIO_MIN_H = 80;
const PORTFOLIO_MAX_H = 700;

const StrategyAgentArea: FC<AgentViewProps> = () => {
  const { t } = useTranslation();
  const { data: strategies = [], isLoading: isLoadingStrategies } =
    useGetStrategyList();
  const [selectedStrategy, setSelectedStrategy] = useState<Strategy | null>(
    null,
  );
  const [mobileTab, setMobileTab] = useState<MobileTab>("strategies");
  const [portfolioHeight, setPortfolioHeight] = useState(PORTFOLIO_DEFAULT_H);
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null);

  // Attach drag listeners only while dragging so we never block global scroll
  const startDrag = (clientY: number) => {
    dragRef.current = { startY: clientY, startHeight: portfolioHeight };

    const onMove = (y: number) => {
      if (!dragRef.current) return;
      const next = dragRef.current.startHeight + (y - dragRef.current.startY);
      setPortfolioHeight(
        Math.min(PORTFOLIO_MAX_H, Math.max(PORTFOLIO_MIN_H, next)),
      );
    };

    const onMouseMove = (e: MouseEvent) => onMove(e.clientY);
    const onTouchMove = (e: TouchEvent) => {
      e.preventDefault();
      onMove(e.touches[0].clientY);
    };
    const onEnd = () => {
      dragRef.current = null;
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("touchmove", onTouchMove);
      window.removeEventListener("mouseup", onEnd);
      window.removeEventListener("touchend", onEnd);
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("touchmove", onTouchMove, { passive: false });
    window.addEventListener("mouseup", onEnd);
    window.addEventListener("touchend", onEnd);
  };

  const { data: composes = [] } = useGetStrategyDetails(
    selectedStrategy?.strategy_id,
  );

  const { data: priceCurve = [] } = useGetStrategyPriceCurve(
    selectedStrategy?.strategy_id,
  );
  const { data: positions = [] } = useGetStrategyHoldings(
    selectedStrategy?.strategy_id,
  );
  const { data: summary } = useGetStrategyPortfolioSummary(
    selectedStrategy?.strategy_id,
  );

  const { mutateAsync: stopStrategy } = useStopStrategy();
  const { mutateAsync: deleteStrategy } = useDeleteStrategy();

  useEffect(() => {
    if (strategies.length === 0) {
      setSelectedStrategy(null);
      return;
    }

    const hasSelectedStrategy =
      selectedStrategy &&
      strategies.some(
        (strategy) => strategy.strategy_id === selectedStrategy.strategy_id,
      );

    if (!selectedStrategy || !hasSelectedStrategy) {
      setSelectedStrategy(strategies[0]);
    }
  }, [strategies, selectedStrategy]);

  if (isLoadingStrategies) return null;

  const mobileTabs: { key: MobileTab; label: string }[] = [
    { key: "strategies", label: t("strategy.tabs.strategies") },
    { key: "history", label: t("strategy.tabs.history") },
  ];

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-muted/30">
      {/* Mobile-only: Portfolio always shown at top, independently scrollable */}
      {selectedStrategy && (
        <div
          className="shrink-0 overflow-y-auto bg-card md:hidden"
          style={{ height: portfolioHeight }}
        >
          <PortfolioPositionsGroup
            summary={summary}
            priceCurve={priceCurve}
            positions={positions}
            strategy={selectedStrategy}
          />
        </div>
      )}

      {/* Drag handle — lets user resize the portfolio panel vs tab content */}
      {selectedStrategy && (
        <div
          className="md:hidden shrink-0 flex h-6 cursor-row-resize select-none touch-none items-center justify-center border-y bg-card"
          onMouseDown={(e) => {
            e.preventDefault();
            startDrag(e.clientY);
          }}
          onTouchStart={(e) => startDrag(e.touches[0].clientY)}
        >
          <GripHorizontal className="size-4 text-muted-foreground/40" />
        </div>
      )}

      {/* Mobile-only tab bar — 2 tabs: Strategies | History */}
      <div className="flex shrink-0 border-b bg-card md:hidden">
        {mobileTabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setMobileTab(tab.key)}
            className={`flex-1 border-b-2 py-3 text-sm font-medium transition-colors ${
              mobileTab === tab.key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content panels */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Strategy list — full-width on mobile (strategies tab), fixed sidebar on desktop */}
        <div
          className={`flex-col gap-4 border-r bg-card py-6 *:px-6 w-full md:w-96 md:flex ${
            mobileTab === "strategies" ? "flex" : "hidden"
          }`}
        >
          <p className="font-semibold text-base">{t("strategy.title")}</p>

          {strategies && strategies.length > 0 ? (
            <TradeStrategyGroup
              strategies={strategies}
              selectedStrategy={selectedStrategy}
              onStrategySelect={(strategy) => {
                setSelectedStrategy(strategy);
                // On mobile, jump to history after selecting a strategy
                setMobileTab("history");
              }}
              onStrategyStop={async (strategyId) =>
                await stopStrategy(strategyId)
              }
              onStrategyDelete={async (strategyId) => {
                await deleteStrategy(strategyId);
              }}
            />
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-4">
              <EmptyIllustration />
              <div className="flex flex-col gap-3 text-center text-base text-muted-foreground">
                <p>{t("strategy.noStrategies")}</p>
                <p>{t("strategy.createFirst")}</p>
              </div>

              <CreateStrategyModal>
                <Button
                  variant="outline"
                  className="w-full gap-3 rounded-lg py-4 text-base"
                >
                  <Plus className="size-6" />
                  {t("strategy.add")}
                </Button>
              </CreateStrategyModal>
            </div>
          )}
        </div>

        {/* Middle + Right: History + Portfolio */}
        <div
          className={`flex flex-1 overflow-hidden md:flex ${
            mobileTab === "strategies" ? "hidden" : "flex"
          }`}
        >
          {selectedStrategy ? (
            <>
              {/* History panel — full-width on mobile, fixed 420px on desktop */}
              <div className="flex w-full md:w-auto">
                <StrategyComposeList
                  composes={composes}
                  tradingMode={selectedStrategy.trading_mode}
                />
              </div>

              {/* Portfolio panel — desktop only (shown at top on mobile) */}
              <div className="hidden md:flex flex-1">
                <PortfolioPositionsGroup
                  summary={summary}
                  priceCurve={priceCurve}
                  positions={positions}
                  strategy={selectedStrategy}
                />
              </div>
            </>
          ) : (
            <div className="flex size-full flex-col items-center justify-center gap-8">
              <EmptyIllustration />
              <p className="font-normal text-base text-muted-foreground">
                {t("strategy.noStrategies")}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default StrategyAgentArea;
