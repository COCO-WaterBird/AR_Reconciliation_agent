"""
Mail sources behind one interface: a synthetic local mailbox and Gmail.

Both return raw RFC 5322 messages (headers, body and attachments), which the
existing document store and parsers already understand.

Gmail access is optional and read-only:

* scope ``https://www.googleapis.com/auth/gmail.readonly`` - the client can
  never send, modify or delete mail;
* OAuth installed-app ("desktop") flow; the client secret file path comes
  from the caller or ``AR_GMAIL_CLIENT_SECRET``; the token is stored next to
  the document store (``data/evidence/gmail_token.json``, gitignored);
* the Google client libraries are an optional extra (``pip install
  ".[gmail]"``) and are imported only when Gmail is used.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

__all__ = ["MailMessage", "MailSource", "LocalMailboxSource", "GmailSource", "GMAIL_SCOPE"]

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


@dataclass(frozen=True)
class MailMessage:
    message_id: str
    raw: bytes


class MailSource(Protocol):
    name: str

    def list_ids(self) -> list[str]: ...

    def fetch(self, message_id: str) -> MailMessage: ...


class LocalMailboxSource:
    """A folder of .eml files - the synthetic demo, no credentials needed."""

    name = "Local mailbox"

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    def list_ids(self) -> list[str]:
        return sorted(p.name for p in self.folder.glob("*.eml"))

    def fetch(self, message_id: str) -> MailMessage:
        return MailMessage(message_id, (self.folder / message_id).read_bytes())


class GmailSource:
    """Read-only Gmail API source (messages matching a search query)."""

    name = "Gmail"

    def __init__(self, query: str = "subject:(payment OR remittance OR remittance advice) newer_than:90d",
                 credentials_path: Path | None = None, token_path: Path | None = None, service=None) -> None:
        self.query = query
        self.credentials_path = Path(credentials_path or os.environ.get("AR_GMAIL_CLIENT_SECRET", "client_secret.json"))
        self.token_path = Path(token_path or "data/evidence/gmail_token.json")
        self._service = service  # injected in tests

    @property
    def service(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Gmail sync needs the optional Google client libraries: "
                "python -m pip install google-api-python-client google-auth-oauthlib"
            ) from exc
        credentials = None
        if self.token_path.exists():
            credentials = Credentials.from_authorized_user_file(str(self.token_path), [GMAIL_SCOPE])
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise RuntimeError("Gmail client secret file not found; see the README (Gmail setup).")
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), [GMAIL_SCOPE])
                credentials = flow.run_local_server(port=0)
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def list_ids(self) -> list[str]:
        ids: list[str] = []
        token = None
        while True:
            response = self.service.users().messages().list(userId="me", q=self.query, pageToken=token).execute()
            ids.extend(m["id"] for m in response.get("messages", []))
            token = response.get("nextPageToken")
            if not token:
                return ids

    def fetch(self, message_id: str) -> MailMessage:
        response = self.service.users().messages().get(userId="me", id=message_id, format="raw").execute()
        raw = base64.urlsafe_b64decode(response["raw"].encode("ascii") + b"=" * (-len(response["raw"]) % 4))
        return MailMessage(message_id, raw)


def iter_new(source: MailSource, processed: Iterable[str]) -> list[str]:
    done = set(processed)
    return [m for m in source.list_ids() if m not in done]
