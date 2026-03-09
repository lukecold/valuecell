import {
  forwardRef,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useRequestMagicLink } from "@/api/auth";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

export interface LoginModalRef {
  open: () => void;
  close: () => void;
}

interface LoginModalProps {
  /** Called after the magic link was successfully sent */
  onSent?: (email: string) => void;
}

const LoginModal = forwardRef<LoginModalRef, LoginModalProps>(
  ({ onSent }, ref) => {
    const { t } = useTranslation();
    const [isOpen, setIsOpen] = useState(false);
    const [email, setEmail] = useState("");
    const [sent, setSent] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    const { mutate: requestLink, isPending } = useRequestMagicLink();

    useImperativeHandle(ref, () => ({
      open: () => {
        setSent(false);
        setEmail("");
        setIsOpen(true);
        setTimeout(() => inputRef.current?.focus(), 50);
      },
      close: () => setIsOpen(false),
    }));

    const handleSubmit = (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = email.trim().toLowerCase();
      if (!trimmed || !trimmed.includes("@")) {
        toast.error(t("auth.magicLink.invalidEmail"));
        return;
      }
      requestLink(trimmed, {
        onSuccess: () => {
          setSent(true);
          onSent?.(trimmed);
        },
        onError: () => {
          toast.error(t("auth.magicLink.sendFailed"));
        },
      });
    };

    return (
      <Dialog open={isOpen} onOpenChange={setIsOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {sent ? t("auth.magicLink.checkEmail") : t("auth.magicLink.title")}
            </DialogTitle>
          </DialogHeader>

          {sent ? (
            <div className="flex flex-col gap-4 py-2">
              <p className="text-muted-foreground text-sm">
                {t("auth.magicLink.sentDesc", { email })}
              </p>
              <Button
                variant="outline"
                className="w-full"
                onClick={() => {
                  setSent(false);
                  setEmail("");
                }}
              >
                {t("auth.magicLink.resend")}
              </Button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="flex flex-col gap-4 py-2">
              <p className="text-muted-foreground text-sm">
                {t("auth.magicLink.desc")}
              </p>
              <Input
                ref={inputRef}
                type="email"
                placeholder={t("auth.magicLink.emailPlaceholder")}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isPending}
                autoComplete="email"
              />
              <Button type="submit" className="w-full" disabled={isPending}>
                {isPending
                  ? t("auth.magicLink.sending")
                  : t("auth.magicLink.sendLink")}
              </Button>
            </form>
          )}
        </DialogContent>
      </Dialog>
    );
  },
);

LoginModal.displayName = "LoginModal";

export default LoginModal;
