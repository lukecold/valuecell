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
  useRestartStrategy,
  useStopStrategy,
} from "@/api/strategy";
import CreateStrategyModal from "@/app/agent/components/strategy-items/modals/create-strategy-modal";
import StrategyChatPanel from "@/app/agent/components/strategy-items/strategy-chat-panel";
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

// Portrait (mobile): 3 top-level tabs — Strategies | History | Chat
type MobileTab = "strategies" | "history" | "chat";
// Landscape (desktop): left sidebar pill — Strategies | Chat
type LeftTab = "strategies" | "chat";

// Mobile portfolio panel height constraints (px)
const PORTFOLIO_DEFAULT_H = 440;
const PORTFOLIO_MIN_H = 80;
const PORTFOLIO_MAX_H = 700;

const StrategyAgentArea: FC<AgentViewProps> = () => {
  const { t } = useTranslation();
  const { data: strategies = [], isLoading: isLoadingStrategies } =
    useGetStrategyList();
  const [selectedStrategy, setSelectedStrategy] = useState<Strategy | null>(
    null,
  );
  // Portrait: which full-screen tab is active
  const [mobileTab, setMobileTab] = useState<MobileTab>("strategies");
  // Landscape: which content is shown in the left sidebar
  const [leftTab, setLeftTab] = useState<LeftTab>("strategies");
  const [portfolioHeight, setPortfolioHeight] = useState(PORTFOLIO_DEFAULT_H);
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null);

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
  const { mutateAsync: restartStrategy } = useRestartStrategy();

  useEffect(() => {
    if (strategies.length === 0) {
      setSelectedStrategy(null);
      return;
    }
    const hasSelectedStrategy =
      selectedStrategy &&
      strategies.some(
        (s) => s.strategy_id === selectedStrategy.strategy_id,
      );
    if (!selectedStrategy || !hasSelectedStrategy) {
      setSelectedStrategy(strategies[0]);
    }
  }, [strategies, selectedStrategy]);

  if (isLoadingStrategies) return null;

  const mobileTabs: { key: MobileTab; label: string }[] = [
    { key: "strategies", label: t("strategy.tabs.strategies") },
    { key: "history", label: t("strategy.tabs.history") },
    { key: "chat", label: t("strategy.tabs.chat") },
  ];

  const leftTabs: { key: LeftTab; label: string }[] = [
    { key: "strategies", label: t("strategy.tabs.strategies") },
    { key: "chat", label: t("strategy.tabs.chat") },
  ];

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-muted/30">
      {/* ── Mobile-only: portfolio strip at top ─────────────────────────────── */}
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

      {selectedStrategy && (
        <div
          className="md:hidden shrink-0 flex h-6 cursor-row-resize select-none touch-none items-center justify-center border-y bg-card"
          onMouseDown={(e) => { e.preventDefault(); startDrag(e.clientY); }}
          onTouchStart={(e) => startDrag(e.touches[0].clientY)}
        >
          <GripHorizontal className="size-4 text-muted-foreground/40" />
        </div>
      )}

      {/* ── Mobile-only tab bar — portrait navigation ────────────────────────── */}
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

      {/* ── Content panels ───────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* LEFT PANEL
            Portrait: visible only on "strategies" tab (strategy list only, no toggle)
            Landscape: always visible, shows Strategies/Chat pill toggle          */}
        <div
          className={`flex-col border-r bg-card w-full md:w-96 md:flex overflow-hidden ${
            mobileTab === "strategies" ? "flex" : "hidden"
          }`}
        >
          {/* Header — pill toggle is desktop-only */}
          <div className="flex shrink-0 items-center justify-between px-6 py-6">
            <p className="font-semibold text-base">{t("strategy.title")}</p>
            {selectedStrategy && (
              <div className="hidden md:flex gap-1 rounded-lg bg-muted p-1">
                {leftTabs.map((tab) => (
                  <button
                    key={tab.key}
                    type="button"
                    onClick={() => setLeftTab(tab.key)}
                    className={`rounded-md px-3 py-1 text-sm font-medium transition-colors ${
                      leftTab === tab.key
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Desktop chat panel — hidden on mobile (mobile has its own full-screen chat) */}
          {leftTab === "chat" && selectedStrategy && (
            <div className="hidden md:flex flex-1 overflow-hidden">
              <StrategyChatPanel strategyId={selectedStrategy.strategy_id} />
            </div>
          )}

          {/* Strategy list
              Mobile: always shown (this panel is only visible on mobileTab=strategies)
              Desktop: hidden when leftTab=chat                                   */}
          <div
            className={`flex flex-1 flex-col gap-4 overflow-y-auto px-6 pb-6 ${
              leftTab === "chat" && selectedStrategy ? "md:hidden" : ""
            }`}
          >
            {strategies && strategies.length > 0 ? (
              <TradeStrategyGroup
                strategies={strategies}
                selectedStrategy={selectedStrategy}
                onStrategySelect={(strategy) => {
                  setSelectedStrategy(strategy);
                  setMobileTab("history");
                }}
                onStrategyStop={async (strategyId) =>
                  await stopStrategy(strategyId)
                }
                onStrategyDelete={async (strategyId) => {
                  await deleteStrategy(strategyId);
                }}
                onStrategyRestart={async (strategyId) => {
                  await restartStrategy(strategyId);
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
        </div>

        {/* MOBILE CHAT PANEL
            Portrait: full-screen when "Chat" tab active
            Landscape: never shown (md:hidden)                                   */}
        {selectedStrategy && (
          <div
            className={`flex-col flex-1 overflow-hidden md:hidden ${
              mobileTab === "chat" ? "flex" : "hidden"
            }`}
          >
            <StrategyChatPanel strategyId={selectedStrategy.strategy_id} />
          </div>
        )}

        {/* MIDDLE + RIGHT — History + Portfolio
            Portrait: visible on "history" tab only
            Landscape: always visible                                             */}
        <div
          className={`flex flex-1 overflow-hidden md:flex ${
            mobileTab === "history" ? "flex" : "hidden"
          }`}
        >
          {selectedStrategy ? (
            <>
              <StrategyComposeList
                composes={composes}
                tradingMode={selectedStrategy.trading_mode}
              />
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
