import {
  type FC,
  type RefObject,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { useGetPromptHistory } from "@/api/strategy";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { TIME_FORMATS, TimeUtils } from "@/lib/time";
import type { PromptVersion } from "@/types/strategy";

// ---------------------------------------------------------------------------
// Diff algorithm (LCS)
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
// Row types
// ---------------------------------------------------------------------------

interface InlineRow {
  leftNo?: number;
  rightNo?: number;
  type: "unchanged" | "added" | "removed";
  text: string;
}

interface SideBySideRow {
  left?: { lineNo: number; text: string; type: "unchanged" | "removed" };
  right?: { lineNo: number; text: string; type: "unchanged" | "added" };
}

function toInlineRows(diff: DiffLine[]): InlineRow[] {
  const rows: InlineRow[] = [];
  let leftNo = 1;
  let rightNo = 1;
  for (const line of diff) {
    if (line.type === "unchanged") {
      rows.push({ leftNo, rightNo, type: "unchanged", text: line.text });
      leftNo++;
      rightNo++;
    } else if (line.type === "removed") {
      rows.push({ leftNo, type: "removed", text: line.text });
      leftNo++;
    } else {
      rows.push({ rightNo, type: "added", text: line.text });
      rightNo++;
    }
  }
  return rows;
}

function toSideBySideRows(diff: DiffLine[]): SideBySideRow[] {
  const rows: SideBySideRow[] = [];
  let leftNo = 1;
  let rightNo = 1;
  let i = 0;
  while (i < diff.length) {
    if (diff[i].type === "unchanged") {
      rows.push({
        left: { lineNo: leftNo, text: diff[i].text, type: "unchanged" },
        right: { lineNo: rightNo, text: diff[i].text, type: "unchanged" },
      });
      leftNo++;
      rightNo++;
      i++;
    } else {
      const removed: string[] = [];
      const added: string[] = [];
      while (i < diff.length && diff[i].type !== "unchanged") {
        if (diff[i].type === "removed") removed.push(diff[i].text);
        else added.push(diff[i].text);
        i++;
      }
      const maxLen = Math.max(removed.length, added.length);
      for (let k = 0; k < maxLen; k++) {
        rows.push({
          left:
            removed[k] !== undefined
              ? { lineNo: leftNo + k, text: removed[k], type: "removed" }
              : undefined,
          right:
            added[k] !== undefined
              ? { lineNo: rightNo + k, text: added[k], type: "added" }
              : undefined,
        });
      }
      leftNo += removed.length;
      rightNo += added.length;
    }
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Line number cell
// ---------------------------------------------------------------------------

const LineNo: FC<{ n?: number }> = ({ n }) => (
  <span className="inline-block w-10 shrink-0 select-none pr-3 text-right font-mono text-[11px] text-muted-foreground/40">
    {n ?? ""}
  </span>
);

// ---------------------------------------------------------------------------
// Inline view
// ---------------------------------------------------------------------------

const InlineView: FC<{ rows: InlineRow[] }> = ({ rows }) => (
  <div className="w-full font-mono text-xs leading-5">
    {rows.map((row, idx) => {
      const bg =
        row.type === "added"
          ? "bg-green-500/10"
          : row.type === "removed"
            ? "bg-red-500/10"
            : "";
      const fg =
        row.type === "added"
          ? "text-green-700 dark:text-green-400"
          : row.type === "removed"
            ? "text-red-700 dark:text-red-400"
            : "text-foreground/80";
      const prefix =
        row.type === "added" ? "+" : row.type === "removed" ? "−" : " ";
      return (
        // biome-ignore lint/suspicious/noArrayIndexKey: stable diff output
        <div key={idx} className={`flex min-w-0 items-start py-px ${bg}`}>
          <LineNo n={row.leftNo} />
          <LineNo n={row.rightNo} />
          <span className={`w-4 shrink-0 select-none text-center font-bold ${fg}`}>
            {prefix}
          </span>
          <span className={`min-w-0 flex-1 whitespace-pre-wrap break-all pl-2 ${fg}`}>
            {row.text || "\u00A0"}
          </span>
        </div>
      );
    })}
  </div>
);

// ---------------------------------------------------------------------------
// Side-by-side view
// ---------------------------------------------------------------------------

const SplitView: FC<{ rows: SideBySideRow[] }> = ({ rows }) => (
  <div className="w-full font-mono text-xs leading-5">
    {rows.map((row, idx) => {
      const leftBg = row.left?.type === "removed" ? "bg-red-500/10" : "";
      const rightBg = row.right?.type === "added" ? "bg-green-500/10" : "";
      return (
        // biome-ignore lint/suspicious/noArrayIndexKey: stable diff output
        <div key={idx} className="flex min-w-0 items-stretch">
          <div className={`flex min-w-0 flex-1 items-start border-r border-border py-px ${leftBg}`}>
            <LineNo n={row.left?.lineNo} />
            <span className={`w-4 shrink-0 select-none text-center font-bold ${row.left?.type === "removed" ? "text-red-700 dark:text-red-400" : ""}`}>
              {row.left?.type === "removed" ? "−" : " "}
            </span>
            <span className={`min-w-0 flex-1 whitespace-pre-wrap break-all pl-2 ${row.left?.type === "removed" ? "text-red-700 dark:text-red-400" : "text-foreground/80"}`}>
              {row.left !== undefined ? row.left.text || "\u00A0" : "\u00A0"}
            </span>
          </div>
          <div className={`flex min-w-0 flex-1 items-start py-px ${rightBg}`}>
            <LineNo n={row.right?.lineNo} />
            <span className={`w-4 shrink-0 select-none text-center font-bold ${row.right?.type === "added" ? "text-green-700 dark:text-green-400" : ""}`}>
              {row.right?.type === "added" ? "+" : " "}
            </span>
            <span className={`min-w-0 flex-1 whitespace-pre-wrap break-all pl-2 ${row.right?.type === "added" ? "text-green-700 dark:text-green-400" : "text-foreground/80"}`}>
              {row.right !== undefined ? row.right.text || "\u00A0" : "\u00A0"}
            </span>
          </div>
        </div>
      );
    })}
  </div>
);

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
// Constants
// ---------------------------------------------------------------------------

type ViewMode = "inline" | "split";
const DEFAULT_W = 920;
const DEFAULT_H = 600;

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const PromptHistoryModal: FC<PromptHistoryModalProps> = ({ ref }) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [strategyId, setStrategyId] = useState<string | number | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("split");

  // Window size (drag-to-resize)
  const [size, setSize] = useState({ width: DEFAULT_W, height: DEFAULT_H });
  const sizeRef = useRef({ width: DEFAULT_W, height: DEFAULT_H });

  // Window position — null = use default CSS centering, otherwise px from viewport TL
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const posRef = useRef<{ top: number; left: number } | null>(null);

  // A/B selection (0-based, newest-first)
  const [selA, setSelA] = useState<number>(1);
  const [selB, setSelB] = useState<number>(0);

  // ── Open / close ──────────────────────────────────────────────────────────

  useImperativeHandle(ref, () => ({
    open: (id) => {
      setStrategyId(id);
      // Center the window in the viewport on each open
      const w = Math.min(sizeRef.current.width, window.innerWidth * 0.96);
      const h = Math.min(sizeRef.current.height, window.innerHeight * 0.96);
      const initial = {
        top: Math.max(0, (window.innerHeight - h) / 2),
        left: Math.max(0, (window.innerWidth - w) / 2),
      };
      posRef.current = initial;
      setPos(initial);
      setIsOpen(true);
    },
  }));

  const handleOpenChange = (open: boolean) => {
    setIsOpen(open);
    if (!open) {
      setStrategyId(null);
      setSelA(1);
      setSelB(0);
    }
  };

  // ── Data ──────────────────────────────────────────────────────────────────

  const { data: versionsRaw = [], isLoading } = useGetPromptHistory(
    strategyId ?? undefined,
    isOpen,
  );

  const versions = useMemo(() => [...versionsRaw].reverse(), [versionsRaw]);

  const versionCount = versions.length;
  const effectiveSelA = Math.min(
    versionCount <= 1 ? 0 : selA,
    Math.max(0, versionCount - 1),
  );
  const effectiveSelB = Math.min(selB, Math.max(0, versionCount - 1));
  const versionA = versions[effectiveSelA];
  const versionB = versions[effectiveSelB];

  const diff = useMemo(
    () =>
      versionA && versionB && effectiveSelA !== effectiveSelB
        ? computeLineDiff(versionA.prompt_text, versionB.prompt_text)
        : null,
    [versionA, versionB, effectiveSelA, effectiveSelB],
  );
  const inlineRows = useMemo(() => (diff ? toInlineRows(diff) : null), [diff]);
  const splitRows = useMemo(() => (diff ? toSideBySideRows(diff) : null), [diff]);

  // ── Drag-to-move (header) ─────────────────────────────────────────────────

  const handleHeaderMouseDown = (e: React.MouseEvent) => {
    // Ignore clicks on interactive children (buttons, etc.)
    if ((e.target as HTMLElement).closest("button, [role='button']")) return;
    e.preventDefault();

    const startX = e.clientX;
    const startY = e.clientY;
    const startTop = posRef.current?.top ?? 0;
    const startLeft = posRef.current?.left ?? 0;

    const onMove = (ev: MouseEvent) => {
      const w = sizeRef.current.width;
      const h = sizeRef.current.height;
      const newTop = Math.max(
        0,
        Math.min(window.innerHeight - 40, startTop + ev.clientY - startY),
      );
      const newLeft = Math.max(
        -w + 80,
        Math.min(window.innerWidth - 80, startLeft + ev.clientX - startX),
      );
      const next = { top: newTop, left: newLeft };
      posRef.current = next;
      setPos(next);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // ── Drag-to-resize (corner handle) ───────────────────────────────────────

  const handleResizeMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation(); // don't trigger header drag

    const startX = e.clientX;
    const startY = e.clientY;
    const startW = sizeRef.current.width;
    const startH = sizeRef.current.height;

    const onMove = (ev: MouseEvent) => {
      const w = Math.max(520, Math.min(window.innerWidth * 0.96, startW + ev.clientX - startX));
      const h = Math.max(380, Math.min(window.innerHeight * 0.96, startH + ev.clientY - startY));
      sizeRef.current = { width: w, height: h };
      setSize({ width: w, height: h });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // ── Helper ────────────────────────────────────────────────────────────────

  const displayVer = (idx: number) => versionsRaw.length - idx;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Dialog open={isOpen} onOpenChange={handleOpenChange}>
      <DialogContent
        // IMPORTANT: do NOT add `relative` here — it overrides the built-in
        // `fixed` positioning and breaks centering. Use no positioning class.
        className="flex flex-col gap-0 overflow-hidden p-0"
        style={
          pos
            ? {
                // Override the default top-[50%] left-[50%] translate(-50%,-50%)
                // with absolute pixel coordinates so we can drag the window.
                top: pos.top,
                left: pos.left,
                transform: "none",
                width: size.width,
                height: size.height,
                maxWidth: "96vw",
                maxHeight: "96vh",
              }
            : {
                width: size.width,
                height: size.height,
                maxWidth: "96vw",
                maxHeight: "96vh",
              }
        }
      >
        {/* ── Header (drag handle + title + view toggle) ─────────────── */}
        <div
          onMouseDown={handleHeaderMouseDown}
          className="flex shrink-0 cursor-move select-none items-center justify-between border-b px-5 py-3"
        >
          <DialogTitle className="text-sm font-semibold">
            {t("strategy.promptHistory.title")}
          </DialogTitle>

          {/* Inline / Split toggle */}
          <div className="mr-6 flex overflow-hidden rounded-md border border-border text-xs">
            <button
              type="button"
              onClick={() => setViewMode("inline")}
              className={`px-3 py-1 transition-colors ${
                viewMode === "inline"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {t("strategy.promptHistory.inlineView")}
            </button>
            <button
              type="button"
              onClick={() => setViewMode("split")}
              className={`border-l border-border px-3 py-1 transition-colors ${
                viewMode === "split"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {t("strategy.promptHistory.splitView")}
            </button>
          </div>
        </div>

        {/* ── Body ───────────────────────────────────────────────────── */}
        {isLoading ? (
          <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
            {t("strategy.promptHistory.loading")}
          </div>
        ) : versions.length === 0 ? (
          <div className="flex flex-1 items-center justify-center p-8 text-center">
            <p className="max-w-xs text-muted-foreground text-sm">
              {t("strategy.promptHistory.empty")}
            </p>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 overflow-hidden">
            {/* Left: version list */}
            <div className="flex w-52 shrink-0 flex-col overflow-y-auto border-r">
              <div className="shrink-0 border-b bg-muted/30 px-4 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("strategy.promptHistory.versions")}
              </div>

              {versions.map((v, idx) => {
                const isA = idx === effectiveSelA;
                const isB = idx === effectiveSelB;
                return (
                  <div
                    key={v.version}
                    className={`flex flex-col gap-1 border-b px-3 py-2.5 transition-colors ${
                      isA || isB ? "bg-muted/50" : "hover:bg-muted/20"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-1">
                      <span className="text-xs font-semibold text-foreground">
                        v{displayVer(idx)}
                        {v.is_current && (
                          <span className="ml-1.5 rounded bg-primary/10 px-1 py-0.5 font-medium text-primary text-[10px]">
                            {t("strategy.promptHistory.current")}
                          </span>
                        )}
                      </span>
                      <div className="flex gap-1">
                        <button
                          type="button"
                          onClick={() => setSelA(idx)}
                          className={`min-w-[20px] rounded px-1.5 py-0.5 text-[10px] font-bold transition-colors ${
                            isA
                              ? "bg-blue-500 text-white"
                              : "bg-muted text-muted-foreground hover:bg-muted-foreground/20"
                          }`}
                        >
                          A
                        </button>
                        <button
                          type="button"
                          onClick={() => setSelB(idx)}
                          className={`min-w-[20px] rounded px-1.5 py-0.5 text-[10px] font-bold transition-colors ${
                            isB
                              ? "bg-green-500 text-white"
                              : "bg-muted text-muted-foreground hover:bg-muted-foreground/20"
                          }`}
                        >
                          B
                        </button>
                      </div>
                    </div>

                    {v.saved_at && (
                      <span className="text-[10px] text-muted-foreground">
                        {TimeUtils.formatUTC(v.saved_at, TIME_FORMATS.DATETIME)}
                      </span>
                    )}

                    <p className="line-clamp-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
                      {v.prompt_text || "—"}
                    </p>
                  </div>
                );
              })}
            </div>

            {/* Right: diff area */}
            <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
              {/* Sub-header: A→B badges */}
              <div className="flex shrink-0 items-center gap-2 border-b bg-muted/20 px-4 py-2 text-xs">
                {versionA && (
                  <Badge
                    variant="outline"
                    className="border-blue-400 font-mono text-blue-600 dark:text-blue-400"
                  >
                    A v{displayVer(effectiveSelA)}
                    {versionA.is_current &&
                      ` · ${t("strategy.promptHistory.current")}`}
                  </Badge>
                )}
                <span className="text-muted-foreground">→</span>
                {versionB && (
                  <Badge
                    variant="outline"
                    className="border-green-400 font-mono text-green-600 dark:text-green-400"
                  >
                    B v{displayVer(effectiveSelB)}
                    {versionB.is_current &&
                      ` · ${t("strategy.promptHistory.current")}`}
                  </Badge>
                )}
                {viewMode === "split" && diff && (
                  <div className="ml-auto flex flex-1 justify-around text-[10px] font-medium text-muted-foreground/60">
                    <span className="flex-1 pl-10 text-blue-500/70">A (old)</span>
                    <span className="flex-1 pl-10 text-green-500/70">B (new)</span>
                  </div>
                )}
              </div>

              {/* Diff content */}
              <div className="min-h-0 flex-1 overflow-auto">
                {versionCount <= 1 ? (
                  <div className="flex h-full items-center justify-center p-8 text-center">
                    <p className="max-w-xs text-muted-foreground text-sm">
                      {t("strategy.promptHistory.onlyOne")}
                    </p>
                  </div>
                ) : effectiveSelA === effectiveSelB ? (
                  <div className="font-mono text-xs leading-5">
                    {(versionA?.prompt_text || "").split("\n").map((line, idx) => (
                      // biome-ignore lint/suspicious/noArrayIndexKey: stable
                      <div key={idx} className="flex min-w-0 items-start py-px">
                        <LineNo n={idx + 1} />
                        <LineNo />
                        <span className="w-4 shrink-0 select-none text-center text-muted-foreground/40"> </span>
                        <span className="min-w-0 flex-1 whitespace-pre-wrap break-all pl-2 text-foreground/80">
                          {line || "\u00A0"}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : viewMode === "inline" ? (
                  <InlineView rows={inlineRows!} />
                ) : (
                  <SplitView rows={splitRows!} />
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── Resize handle (bottom-right corner) ──────────────────── */}
        <div
          onMouseDown={handleResizeMouseDown}
          className="absolute bottom-0 right-0 z-20 cursor-nwse-resize p-1.5 opacity-30 transition-opacity hover:opacity-80"
          style={{ userSelect: "none" }}
          title="Drag to resize"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="currentColor"
            className="text-muted-foreground"
          >
            <rect x="6" y="10" width="2" height="2" rx="0.5" />
            <rect x="10" y="10" width="2" height="2" rx="0.5" />
            <rect x="10" y="6" width="2" height="2" rx="0.5" />
          </svg>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default PromptHistoryModal;
