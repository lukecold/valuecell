import { type FC, type RefObject, useImperativeHandle, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useGetPromptHistory } from "@/api/strategy";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { TIME_FORMATS, TimeUtils } from "@/lib/time";
import type { PromptVersion } from "@/types/strategy";

// ---------------------------------------------------------------------------
// LCS line diff (same algorithm as strategy-chat-panel)
// ---------------------------------------------------------------------------

interface DiffLine {
  type: "unchanged" | "added" | "removed";
  text: string;
}

function computeLineDiff(original: string, proposed: string): DiffLine[] {
  const a = original.split("\n");
  const b = proposed.split("\n");
  const m = a.length;
  const n = b.length;

  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array(n + 1).fill(0),
  );
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] =
        a[i - 1] === b[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  const result: DiffLine[] = [];
  let i = m;
  let j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      result.unshift({ type: "unchanged", text: a[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      result.unshift({ type: "added", text: b[j - 1] });
      j--;
    } else {
      result.unshift({ type: "removed", text: a[i - 1] });
      i--;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// DiffView
// ---------------------------------------------------------------------------

const DiffView: FC<{ versionA: PromptVersion; versionB: PromptVersion }> = ({
  versionA,
  versionB,
}) => {
  const diff = useMemo(
    () => computeLineDiff(versionA.prompt_text, versionB.prompt_text),
    [versionA.prompt_text, versionB.prompt_text],
  );

  const hasChanges = diff.some((l) => l.type !== "unchanged");

  return (
    <div className="flex flex-col gap-2 overflow-hidden flex-1 min-h-0">
      <div className="rounded-md border border-border bg-muted/40 flex-1 overflow-y-auto font-mono text-xs leading-5 min-h-0">
        {hasChanges ? (
          diff.map((line, idx) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: stable diff output
            <div
              key={idx}
              className={
                line.type === "added"
                  ? "bg-green-500/10 text-green-700 dark:text-green-400 px-2"
                  : line.type === "removed"
                    ? "bg-red-500/10 text-red-700 dark:text-red-400 px-2"
                    : "text-muted-foreground px-2"
              }
            >
              <span className="mr-2 select-none opacity-60">
                {line.type === "added"
                  ? "+"
                  : line.type === "removed"
                    ? "−"
                    : " "}
              </span>
              {line.text || "\u00A0"}
            </div>
          ))
        ) : (
          <div className="flex h-full items-center justify-center text-muted-foreground p-4">
            No differences between selected versions
          </div>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Public ref
// ---------------------------------------------------------------------------

export interface PromptHistoryModalRef {
  open: (strategyId: string | number) => void;
}

interface PromptHistoryModalProps {
  ref?: RefObject<PromptHistoryModalRef | null>;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const PromptHistoryModal: FC<PromptHistoryModalProps> = ({ ref }) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [strategyId, setStrategyId] = useState<string | number | null>(null);

  // A and B are 0-based indices into the versions array (newest-first)
  // We show newest first, so index 0 = most recent
  const [selA, setSelA] = useState<number>(1); // second-latest = "before"
  const [selB, setSelB] = useState<number>(0); // latest = "after"

  useImperativeHandle(ref, () => ({
    open: (id) => {
      setStrategyId(id);
      setIsOpen(true);
    },
  }));

  const { data: versionsRaw = [], isLoading } = useGetPromptHistory(
    strategyId ?? undefined,
    isOpen,
  );

  // Reverse so newest is first in the list
  const versions = useMemo(
    () => [...versionsRaw].reverse(),
    [versionsRaw],
  );

  // Reset selection when versions change (new strategy opened)
  const versionCount = versions.length;
  const safeSelA = Math.min(selA, versionCount - 1);
  const safeSelB = Math.min(selB, versionCount - 1);

  // For comparison: A is "from", B is "to"
  // The user picks two versions; we diff A→B
  const versionA = versions[safeSelA];
  const versionB = versions[safeSelB];

  const handleOpen = (isNowOpen: boolean) => {
    setIsOpen(isNowOpen);
    if (!isNowOpen) {
      setStrategyId(null);
      setSelA(1);
      setSelB(0);
    }
  };

  // When versions load, default selection: B=current(0), A=previous(1)
  // Already set as initial state, but reset if only 1 version
  const effectiveSelA = versionCount <= 1 ? 0 : safeSelA;
  const effectiveSelB = safeSelB;
  const versionAEff = versions[effectiveSelA];
  const versionBEff = versions[effectiveSelB];

  return (
    <Dialog open={isOpen} onOpenChange={handleOpen}>
      <DialogContent className="flex max-h-[85vh] w-full max-w-4xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b px-6 py-4">
          <DialogTitle>{t("strategy.promptHistory.title")}</DialogTitle>
        </DialogHeader>

        {isLoading ? (
          <div className="flex flex-1 items-center justify-center p-8 text-muted-foreground text-sm">
            {t("strategy.promptHistory.loading")}
          </div>
        ) : versions.length === 0 ? (
          <div className="flex flex-1 items-center justify-center p-8 text-center">
            <p className="text-muted-foreground text-sm">
              {t("strategy.promptHistory.empty")}
            </p>
          </div>
        ) : (
          <div className="flex flex-1 overflow-hidden min-h-0">
            {/* Left: version list */}
            <div className="flex w-56 shrink-0 flex-col overflow-y-auto border-r">
              <p className="shrink-0 px-4 py-2 text-muted-foreground text-xs font-medium border-b bg-muted/30">
                {t("strategy.promptHistory.versions")}
              </p>
              {versions.map((v, idx) => {
                const isSelA = idx === effectiveSelA;
                const isSelB = idx === effectiveSelB;
                // Original version number (newest = highest)
                const displayVersion = versionsRaw.length - idx;
                return (
                  <div
                    key={v.version}
                    className={`flex flex-col gap-1.5 border-b px-4 py-3 transition-colors ${
                      isSelA || isSelB ? "bg-muted/50" : "hover:bg-muted/20"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-1">
                      <span className="font-medium text-foreground text-xs">
                        v{displayVersion}
                        {v.is_current && (
                          <span className="ml-1.5 rounded-sm bg-primary/10 px-1 py-0.5 text-primary text-[10px] font-medium">
                            {t("strategy.promptHistory.current")}
                          </span>
                        )}
                      </span>
                      <div className="flex gap-1">
                        <button
                          type="button"
                          onClick={() => setSelA(idx)}
                          className={`rounded px-1.5 py-0.5 text-[10px] font-bold transition-colors ${
                            isSelA
                              ? "bg-blue-500 text-white"
                              : "bg-muted text-muted-foreground hover:bg-muted-foreground/20"
                          }`}
                        >
                          A
                        </button>
                        <button
                          type="button"
                          onClick={() => setSelB(idx)}
                          className={`rounded px-1.5 py-0.5 text-[10px] font-bold transition-colors ${
                            isSelB
                              ? "bg-green-500 text-white"
                              : "bg-muted text-muted-foreground hover:bg-muted-foreground/20"
                          }`}
                        >
                          B
                        </button>
                      </div>
                    </div>
                    {v.saved_at && (
                      <span className="text-muted-foreground text-[10px]">
                        {TimeUtils.formatUTC(v.saved_at, TIME_FORMATS.DATETIME)}
                      </span>
                    )}
                    <p className="line-clamp-2 text-muted-foreground text-[10px] leading-relaxed font-mono">
                      {v.prompt_text || "—"}
                    </p>
                  </div>
                );
              })}
            </div>

            {/* Right: diff view */}
            <div className="flex flex-1 flex-col overflow-hidden p-4 gap-3 min-h-0">
              {/* Comparison header */}
              <div className="flex shrink-0 items-center gap-2 text-xs">
                <Badge
                  variant="outline"
                  className="border-blue-400 text-blue-600 dark:text-blue-400"
                >
                  A: v{versionAEff ? versionsRaw.length - effectiveSelA : "?"}
                  {versionAEff?.is_current && ` (${t("strategy.promptHistory.current")})`}
                </Badge>
                <span className="text-muted-foreground">→</span>
                <Badge
                  variant="outline"
                  className="border-green-400 text-green-600 dark:text-green-400"
                >
                  B: v{versionBEff ? versionsRaw.length - effectiveSelB : "?"}
                  {versionBEff?.is_current && ` (${t("strategy.promptHistory.current")})`}
                </Badge>
                <span className="ml-auto text-muted-foreground">
                  {t("strategy.promptHistory.diffHint")}
                </span>
              </div>

              {versionAEff && versionBEff ? (
                effectiveSelA === effectiveSelB ? (
                  <div className="flex-1 min-h-0 overflow-y-auto rounded-md border border-border bg-muted/40 font-mono text-xs leading-5 p-2">
                    {(versionAEff.prompt_text || "").split("\n").map((line, idx) => (
                      // biome-ignore lint/suspicious/noArrayIndexKey: stable
                      <div key={idx} className="text-muted-foreground px-2">
                        <span className="mr-2 select-none opacity-60"> </span>
                        {line || "\u00A0"}
                      </div>
                    ))}
                  </div>
                ) : (
                  <DiffView versionA={versionAEff} versionB={versionBEff} />
                )
              ) : (
                <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
                  {t("strategy.promptHistory.selectTwo")}
                </div>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default PromptHistoryModal;
