import hashlib
import hmac
import time
from dataclasses import dataclass
import requests
from typing import Optional
from logging_utils import get_logger
from config import (
    API_KEY, SECRET_KEY, ROOSTOO_BASE_URL, REQUEST_TIMEOUT_SECONDS,
    READ_RETRIES, RETRY_BASE_SECONDS, MAX_RETRY_DELAY_SECONDS,
    SERVER_TIME_REFRESH_SECONDS, MAX_CLOCK_OFFSET_MS,
)

log = get_logger("roostoo.api")

class APIError(RuntimeError):
    pass

class APIUnavailable(APIError):
    pass

class AuthenticationError(APIError):
    pass

class OrderOutcomeUnknown(APIError):
    pass

@dataclass
class RoostooClient:
    session: Optional[requests.Session] = None

    def __post_init__(self):
        self.session = self.session or requests.Session()
        self.offset_ms = 0
        self.last_time_sync = 0.0

    def _url(self, path):
        return ROOSTOO_BASE_URL + path

    def _json(self, response, label):
        if response.status_code in (401, 403):
            raise AuthenticationError(f"{label}: HTTP {response.status_code}")
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise APIError(f"{label}: non-JSON response") from exc
        if not isinstance(data, dict):
            raise APIError(f"{label}: response is not an object")
        if data.get("Success") is False:
            raise APIError(f"{label}: {data.get('ErrMsg', 'request failed')}")
        return data

    def _reset_session(self):
        old = self.session
        self.session = requests.Session()
        try:
            old.close()
        except Exception:
            pass
        log.warning("HTTP_SESSION_RESET")

    def _safe_get(self, path, params=None, label="GET"):
        delay = RETRY_BASE_SECONDS
        started = time.monotonic()
        last = None
        log.info("API_REQUEST_START | method=GET | label=%s | path=%s", label, path)
        for attempt in range(READ_RETRIES + 1):
            try:
                response = self.session.get(
                    self._url(path),
                    params=params or {},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                data = self._json(response, label)
                log.info(
                    "API_REQUEST_OK | method=GET | label=%s | elapsed_ms=%d",
                    label, int((time.monotonic() - started) * 1000)
                )
                return data
            except AuthenticationError:
                raise
            except (requests.RequestException, APIError) as exc:
                last = exc
                if attempt >= READ_RETRIES:
                    self._reset_session()
                    break
                log.warning(
                    "READ_RETRY | label=%s | attempt=%d | error=%s",
                    label, attempt + 1, exc
                )
                time.sleep(min(delay, MAX_RETRY_DELAY_SECONDS))
                delay *= 2
        log.error(
            "API_REQUEST_FAILED | method=GET | label=%s | elapsed_ms=%d | error=%s",
            label, int((time.monotonic() - started) * 1000), last
        )
        raise APIUnavailable(f"{label} failed: {last}")

    def server_time(self):
        data = self._safe_get("/v3/serverTime", label="server_time")
        value = data.get("ServerTime")
        if not isinstance(value, (int, float)):
            raise APIError("server_time: missing ServerTime")
        return int(value)

    def sync_time(self):
        local = int(time.time() * 1000)
        server = self.server_time()
        self.offset_ms = server - local
        self.last_time_sync = time.monotonic()
        log.info("SERVER_TIME_SYNC | offset_ms=%d", self.offset_ms)
        if abs(self.offset_ms) > MAX_CLOCK_OFFSET_MS:
            log.warning("CLOCK_OFFSET_LARGE | offset_ms=%d", self.offset_ms)
        return self.offset_ms

    def ts(self):
        if time.monotonic() - self.last_time_sync > SERVER_TIME_REFRESH_SECONDS:
            self.sync_time()
        return str(int(time.time() * 1000) + self.offset_ms)

    @staticmethod
    def _sign(payload, secret):
        total = "&".join(f"{k}={payload[k]}" for k in sorted(payload))
        return hmac.new(secret.encode(), total.encode(), hashlib.sha256).hexdigest(), total

    def _signed(self, path, payload, method="POST", label="SIGNED"):
        if not API_KEY or not SECRET_KEY:
            raise AuthenticationError("Missing ROOSTOO_API_KEY/ROOSTOO_SECRET_KEY")
        body = dict(payload)
        body["timestamp"] = self.ts()
        signature, total = self._sign(body, SECRET_KEY)
        headers = {
            "RST-API-KEY": API_KEY,
            "MSG-SIGNATURE": signature,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        started = time.monotonic()
        log.info("API_REQUEST_START | method=%s | label=%s | path=%s", method, label, path)
        try:
            if method == "GET":
                response = self.session.get(
                    self._url(path), headers=headers, params=body,
                    timeout=REQUEST_TIMEOUT_SECONDS
                )
            else:
                response = self.session.post(
                    self._url(path), headers=headers, data=total,
                    timeout=REQUEST_TIMEOUT_SECONDS
                )
            data = self._json(response, label)
            log.info(
                "API_REQUEST_OK | method=%s | label=%s | elapsed_ms=%d",
                method, label, int((time.monotonic() - started) * 1000)
            )
            return data
        except requests.Timeout as exc:
            self._reset_session()
            if label == "place_order":
                raise OrderOutcomeUnknown("place_order timeout; outcome is unknown") from exc
            raise APIUnavailable(f"{label} timeout") from exc
        except requests.RequestException as exc:
            self._reset_session()
            if label == "place_order":
                raise OrderOutcomeUnknown("place_order transport error; outcome is unknown") from exc
            raise APIUnavailable(f"{label}: {exc}") from exc

    def exchange_info(self):
        return self._safe_get("/v3/exchangeInfo", label="exchange_info")

    def ticker(self, pair):
        return self._safe_get(
            "/v3/ticker", {"timestamp": self.ts(), "pair": pair}, label=f"ticker:{pair}"
        )

    def balance(self):
        return self._signed("/v3/balance", {}, "GET", "balance")

    def pending_count(self):
        try:
            return self._signed("/v3/pending_count", {}, "GET", "pending_count")
        except APIError as exc:
            if "no pending order" in str(exc).lower():
                return {"Success": False, "TotalPending": 0, "OrderPairs": {}}
            raise

    def query_order(self, order_id=None, pair=None, pending_only=None):
        payload = {}
        if order_id is not None:
            payload["order_id"] = str(order_id)
        else:
            if pair:
                payload["pair"] = pair
            if pending_only is not None:
                payload["pending_only"] = "TRUE" if pending_only else "FALSE"
        try:
            return self._signed("/v3/query_order", payload, "POST", "query_order")
        except APIError as exc:
            if "no order matched" in str(exc).lower():
                return {"Success": False, "OrderMatched": []}
            raise

    def place_market(self, pair, side, quantity):
        return self._signed(
            "/v3/place_order",
            {"pair": pair, "side": side, "type": "MARKET", "quantity": str(quantity)},
            "POST",
            "place_order",
        )
