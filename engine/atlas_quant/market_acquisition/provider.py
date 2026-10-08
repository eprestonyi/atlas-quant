"""One-attempt official HTTPS adapter; no Tushare retry-wrapper dependency."""

from dataclasses import dataclass
import time
import re
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from .protocol import validate_request
from .protocol import encode, require, utc_now

REQUEST_SECONDS = 30


@dataclass(frozen=True)
class RawResponse:
    raw: bytes
    http_status: int
    retrieved_at: str
    source_kind: str


class OutcomeUnknown(Exception):
    code = "PROVIDER_OUTCOME_UNKNOWN"

    def __init__(self):
        super().__init__("Provider outcome is unknown; no automatic retry is permitted")


class RawMarketAdapter:
    """Only the separate acquisition process receives this configuration.

    A network call has no automatic retry, redirect or alternative host. The
    service durably marks calling before invoking this adapter in a bounded child.
    Response bytes are returned unchanged and must be fsynced before parsing.
    """

    source_kind = "provider"

    def __init__(self, config, session=None):
        self.scope = config.get("authorization_scope")
        require(
            isinstance(self.scope, str)
            and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,79}", self.scope),
            "ACQUISITION_SCOPE",
            "A fixed bounded authorization scope is required",
        )
        access = config.get("provider_access")
        self.proxy = access is not None
        if self.proxy:
            require(
                isinstance(access, dict)
                and set(access) == {"proxyUrl", "serviceToken"}
                and access["proxyUrl"]
                == "https://atlas-aletheia.com/api/internal/atlas-quant/tushare"
                and isinstance(access["serviceToken"], str)
                and 24 <= len(access["serviceToken"]) <= 4096,
                "ACQUISITION_PROVIDER_CONFIG",
                "A fixed approved private HTTPS proxy is required",
            )
            self.url, self.secret = access["proxyUrl"], access["serviceToken"]
        else:
            self.url = "https://api.tushare.pro"
            self.secret = config.get("tushare_token")
            require(
                isinstance(self.secret, str) and 16 <= len(self.secret) <= 4096,
                "ACQUISITION_PROVIDER_CONFIG",
                "An explicit private Tushare token is required",
            )
        parsed = urlsplit(self.url)
        require(
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment,
            "ACQUISITION_PROVIDER_CONFIG",
            "Unsafe provider URL",
        )
        self.session = session or requests.Session()
        if session is None:
            self.session.mount("https://", HTTPAdapter(max_retries=0))
        self.session.trust_env = False

    def call_once(self, request, *, deadline=None, maximum_bytes=None):
        validate_request(request, self.scope)
        maximum = min(
            maximum_bytes if maximum_bytes is not None else request["responseBytes"],
            request["responseBytes"],
        )
        require(
            type(maximum) is int and maximum > 0,
            "ACQUISITION_RESPONSE_BUDGET",
            "No raw response budget remains",
        )
        deadline = min(deadline or float("inf"), time.monotonic() + REQUEST_SECONDS)
        payload = {
            "api_name": request["apiName"],
            "params": request["params"],
            "fields": request["fields"],
        }
        headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        if self.proxy:
            headers["Authorization"] = "Bearer " + self.secret
        else:
            payload["token"] = self.secret
        remaining = deadline - time.monotonic()
        require(
            remaining > 0,
            "ACQUISITION_DEADLINE",
            "Provider request deadline elapsed before send",
        )
        try:
            with self.session.request(
                "POST",
                self.url,
                data=encode(payload),
                headers=headers,
                stream=True,
                allow_redirects=False,
                timeout=(min(10, remaining), min(20, remaining)),
            ) as response:
                # Raw means actual delivered HTTP entity bytes. Compression is
                # refused, not silently decoded into a claimed wire-byte hash.
                if response.headers.get("Content-Encoding", "identity").lower() not in {
                    "",
                    "identity",
                }:
                    raise OutcomeUnknown()
                declared = response.headers.get("Content-Length")
                if declared is not None and (
                    not declared.isdecimal() or int(declared) > maximum
                ):
                    raise OutcomeUnknown()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > maximum or time.monotonic() >= deadline:
                        raise OutcomeUnknown()
                    chunks.append(chunk)
                if declared is not None and size != int(declared):
                    raise OutcomeUnknown()
                return RawResponse(
                    b"".join(chunks), response.status_code, utc_now(), self.source_kind
                )
        except OutcomeUnknown:
            raise
        except (requests.RequestException, OSError, TimeoutError):
            raise OutcomeUnknown() from None


def preflight_provider_config(config):
    """Pure configuration check; no request, token verification or claim."""
    adapter = RawMarketAdapter(config)
    adapter.session.close()
