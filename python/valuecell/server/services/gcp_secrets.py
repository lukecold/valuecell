"""Load GCP Secret Manager secrets into the process environment.

Reads a named secret (multi-line KEY=VALUE format) and injects each pair
into os.environ. Designed for use during server startup so that downstream
services (e.g. strategy auto-resume) can find credentials they need.

The GCP VM's service account is used automatically via Application Default
Credentials (ADC) — no explicit key file is required.

Usage:
    secret_name = os.environ.get("GCP_SECRET_NAME")
    if secret_name:
        load_gcp_secret_to_env(secret_name)
"""

from __future__ import annotations

import os
import urllib.request
from typing import Optional

from loguru import logger


def load_gcp_secret_to_env(
    secret_name: str,
    project_id: Optional[str] = None,
) -> None:
    """Fetch a GCP Secret Manager secret and inject its KEY=VALUE pairs into os.environ.

    Args:
        secret_name: Name of the secret (e.g. "BinanceSecrets").
        project_id: GCP project ID. If None, auto-detected from the GCE
            metadata server (works automatically on Compute Engine VMs).
    """
    try:
        from google.cloud import secretmanager  # type: ignore[import]
    except ImportError:
        logger.warning(
            "GCP secrets: google-cloud-secret-manager not installed; skipping"
        )
        return

    try:
        # Auto-detect project ID from the GCE metadata server if not provided.
        if project_id is None:
            project_id = os.environ.get("GCP_PROJECT_ID") or _detect_project_id()

        if not project_id:
            logger.warning(
                "GCP secrets: could not determine GCP project ID; "
                "set GCP_PROJECT_ID env var to enable secret loading"
            )
            return

        client = secretmanager.SecretManagerServiceClient()
        secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": secret_path})
        payload = response.payload.data.decode("utf-8")

        count = 0
        for line in payload.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key:
                os.environ[key] = value
                count += 1

        logger.info(
            "GCP secrets: loaded {} key(s) from secret '{}'", count, secret_name
        )

    except Exception:
        logger.warning(
            "GCP secrets: failed to load secret '{}' — skipping", secret_name
        )


def _detect_project_id() -> Optional[str]:
    """Query the GCE metadata server to get the current project ID."""
    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/project/project-id",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return None
