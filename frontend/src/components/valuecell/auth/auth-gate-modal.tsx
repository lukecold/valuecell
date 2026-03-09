/**
 * AuthGateModal — pops up when an unauthenticated user tries a restricted action.
 *
 * Usage:
 *   const { requireAuth } = useAuthGate();
 *   <button onClick={() => requireAuth(() => doRestrictedAction())}>Stop</button>
 *   <AuthGateModal />
 */
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useIsLoggedIn } from "@/store/system-store";
import LoginModal, { type LoginModalRef } from "./login-modal";

// ── Context ───────────────────────────────────────────────────────────────────

interface AuthGateContextValue {
  /** Run `callback` if authenticated; otherwise open the auth gate modal. */
  requireAuth: (callback: () => void) => void;
}

const AuthGateContext = createContext<AuthGateContextValue>({
  requireAuth: () => {},
});

export const useAuthGate = () => useContext(AuthGateContext);

// ── Provider + modal ──────────────────────────────────────────────────────────

export function AuthGateProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const isLoggedIn = useIsLoggedIn();

  const loginModalRef = useRef<LoginModalRef>(null);
  const [isGateOpen, setIsGateOpen] = useState(false);
  const pendingCallbackRef = useRef<(() => void) | null>(null);

  const requireAuth = useCallback(
    (callback: () => void) => {
      if (isLoggedIn) {
        callback();
      } else {
        pendingCallbackRef.current = callback;
        setIsGateOpen(true);
      }
    },
    [isLoggedIn],
  );

  const handleSignIn = () => {
    setIsGateOpen(false);
    loginModalRef.current?.open();
  };

  return (
    <AuthGateContext.Provider value={{ requireAuth }}>
      {children}

      {/* Gate dialog — shown before login modal */}
      <Dialog open={isGateOpen} onOpenChange={setIsGateOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("auth.gate.title")}</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <p className="text-muted-foreground text-sm">
              {t("auth.gate.desc")}
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                className="flex-1"
                onClick={() => setIsGateOpen(false)}
              >
                {t("auth.gate.cancel")}
              </Button>
              <Button className="flex-1" onClick={handleSignIn}>
                {t("auth.gate.signIn")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Reusable login modal */}
      <LoginModal ref={loginModalRef} />
    </AuthGateContext.Provider>
  );
}
