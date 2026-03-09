/**
 * /auth/verify?token=xxx
 *
 * Reads the token from the URL, calls the backend to validate it,
 * stores the access token and user info in the system store,
 * then redirects to /home.
 */
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";
import { useVerifyMagicLink } from "@/api/auth";
import { useSystemStore } from "@/store/system-store";

export default function AuthVerifyPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const setSystemInfo = useSystemStore((s) => s.setSystemInfo);

  const { data, isError, isSuccess } = useVerifyMagicLink(token);

  useEffect(() => {
    if (isSuccess && data) {
      setSystemInfo({
        id: data.id,
        email: data.email,
        name: data.name,
        avatar: data.avatar || "",
        access_token: data.access_token,
        refresh_token: data.refresh_token || "",
        created_at: data.created_at || "",
        updated_at: data.updated_at || "",
      });
      toast.success(t("auth.verify.success", { name: data.name || data.email }));
      navigate("/home", { replace: true });
    }
  }, [isSuccess, data, setSystemInfo, navigate, t]);

  useEffect(() => {
    if (isError) {
      toast.error(t("auth.verify.failed"));
      navigate("/home", { replace: true });
    }
  }, [isError, navigate, t]);

  return (
    <div className="flex h-screen w-full items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <div className="size-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        <p className="text-muted-foreground text-sm">
          {token ? t("auth.verify.verifying") : t("auth.verify.noToken")}
        </p>
      </div>
    </div>
  );
}
