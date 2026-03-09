"""Auth router — email magic link authentication."""

from __future__ import annotations

import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from valuecell.server.api.auth_utils import (
    create_access_token,
    require_current_user,
)
from valuecell.server.api.schemas.base import SuccessResponse
from valuecell.server.db.connection import get_db


class MagicLinkRequest(BaseModel):
    email: str


def _send_magic_link_email(to_email: str, token: str, base_url: str) -> bool:
    """Send magic link email. Returns True if sent via SMTP, False on console fallback."""
    verify_url = f"{base_url}/auth/verify?token={token}"

    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    from_email = os.getenv("FROM_EMAIL", smtp_user or "noreply@valuecell.ai")

    if not smtp_host or not smtp_user or not smtp_pass:
        # Dev mode — log to server console
        logger.info(
            "\n=== MAGIC LINK (SMTP not configured) ===\n"
            "To: {}\nLink: {}\n"
            "=========================================",
            to_email,
            verify_url,
        )
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Your ValueCell sign-in link"
        msg["From"] = from_email
        msg["To"] = to_email
        msg.attach(
            MIMEText(
                f"Click to sign in to ValueCell:\n\n{verify_url}\n\n"
                "This link expires in 15 minutes.",
                "plain",
            )
        )
        msg.attach(
            MIMEText(
                f"""<html><body>
                <p>Click below to sign in to ValueCell:</p>
                <p><a href="{verify_url}" style="background:#2563eb;color:white;
                   padding:12px 24px;border-radius:6px;text-decoration:none;
                   display:inline-block;">Sign In</a></p>
                <p>Or paste this link:<br/>
                   <a href="{verify_url}">{verify_url}</a></p>
                <p>Expires in 15 minutes.</p>
                </body></html>""",
                "html",
            )
        )
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())
        logger.info("Magic link email sent to {}", to_email)
        return True
    except Exception as exc:
        logger.error("Failed to send magic link email: {}", exc)
        logger.info("MAGIC LINK fallback — {}: {}", to_email, verify_url)
        return False


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.post("/magic-link", response_model=SuccessResponse)
    async def request_magic_link(
        request: MagicLinkRequest,
        db: Session = Depends(get_db),
    ):
        """Send a magic link to the given email address."""
        email = request.email.strip().lower()
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="Invalid email address")

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

        try:
            db.execute(
                text(
                    """
                    INSERT INTO auth_magic_links (email, token, expires_at, used, created_at)
                    VALUES (:email, :token, :expires_at, false, CURRENT_TIMESTAMP)
                    """
                ),
                {"email": email, "token": token, "expires_at": expires_at},
            )
            db.commit()
        except Exception as exc:
            logger.error("Failed to store magic link token: {}", exc)
            raise HTTPException(status_code=500, detail="Failed to send magic link")

        base_url = os.getenv("APP_BASE_URL", "http://localhost:5173")
        try:
            _send_magic_link_email(email, token, base_url)
        except Exception as exc:
            logger.error("Magic link email error: {}", exc)

        return SuccessResponse.create(
            data={"email": email},
            msg="Magic link sent. Check your email (or server logs in dev mode).",
        )

    @router.get("/verify", response_model=SuccessResponse)
    async def verify_magic_link(
        token: str = Query(..., description="Magic link token"),
        db: Session = Depends(get_db),
    ):
        """Verify a magic link token and return an access token."""
        try:
            row = db.execute(
                text(
                    """
                    SELECT email, expires_at, used
                    FROM auth_magic_links
                    WHERE token = :token
                    """
                ),
                {"token": token},
            ).fetchone()
        except Exception as exc:
            logger.error("DB error in /auth/verify: {}", exc)
            raise HTTPException(status_code=500, detail="Internal error")

        if not row:
            raise HTTPException(status_code=400, detail="Invalid or expired token")

        email, expires_at, used = row[0], row[1], row[2]

        if used:
            raise HTTPException(status_code=400, detail="Token already used")

        now = datetime.now(timezone.utc)
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            raise HTTPException(status_code=400, detail="Token has expired")

        # Mark token as used and ensure user exists
        try:
            db.execute(
                text("UPDATE auth_magic_links SET used = true WHERE token = :token"),
                {"token": token},
            )
            db.execute(
                text(
                    """
                    INSERT INTO auth_users (id, email, created_at)
                    VALUES (:id, :email, CURRENT_TIMESTAMP)
                    ON CONFLICT (email) DO NOTHING
                    """
                ),
                {"id": email, "email": email},
            )
            db.commit()
        except Exception as exc:
            logger.error("Failed to finalize magic link verification: {}", exc)
            raise HTTPException(status_code=500, detail="Internal error")

        access_token = create_access_token(email)
        now_iso = datetime.now(timezone.utc).isoformat()
        return SuccessResponse.create(
            data={
                "id": email,
                "email": email,
                "name": email.split("@")[0],
                "access_token": access_token,
                "refresh_token": "",
                "avatar": "",
                "created_at": now_iso,
                "updated_at": now_iso,
            },
            msg="Authenticated successfully",
        )

    @router.get("/me", response_model=SuccessResponse)
    async def get_me(
        current_user: str = Depends(require_current_user),
        db: Session = Depends(get_db),
    ):
        """Return current authenticated user info."""
        try:
            row = db.execute(
                text(
                    "SELECT id, email, created_at FROM auth_users WHERE email = :email"
                ),
                {"email": current_user},
            ).fetchone()
        except Exception as exc:
            logger.error("DB error in /auth/me: {}", exc)
            raise HTTPException(status_code=500, detail="Internal error")

        # User has a valid token but no DB record (e.g., after DB reset) — auto-create
        if not row:
            try:
                db.execute(
                    text(
                        """
                        INSERT INTO auth_users (id, email, created_at)
                        VALUES (:id, :email, CURRENT_TIMESTAMP)
                        ON CONFLICT (email) DO NOTHING
                        """
                    ),
                    {"id": current_user, "email": current_user},
                )
                db.commit()
            except Exception:
                pass

        now_iso = datetime.now(timezone.utc).isoformat()
        return SuccessResponse.create(
            data={
                "id": current_user,
                "email": current_user,
                "name": current_user.split("@")[0],
                "avatar": "",
                "access_token": "",
                "refresh_token": "",
                "created_at": now_iso,
                "updated_at": now_iso,
            },
            msg="User info retrieved",
        )

    @router.post("/claim-strategies", response_model=SuccessResponse)
    async def claim_unowned_strategies(
        current_user: str = Depends(require_current_user),
        db: Session = Depends(get_db),
    ):
        """Assign all unclaimed strategies (user_id IS NULL or 'default_user') to the current user."""
        try:
            result = db.execute(
                text(
                    """
                    UPDATE strategies
                    SET user_id = :email
                    WHERE user_id IS NULL OR user_id = 'default_user'
                    """
                ),
                {"email": current_user},
            )
            db.commit()
            count = result.rowcount
        except Exception as exc:
            logger.error("Failed to claim strategies: {}", exc)
            raise HTTPException(status_code=500, detail="Internal error")

        return SuccessResponse.create(
            data={"claimed": count, "email": current_user},
            msg=f"Claimed {count} strategy(s) for {current_user}",
        )

    return router
