from __future__ import annotations

import datetime
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import base64
import html
import requests
from bs4 import BeautifulSoup


class BillingApiError(Exception):
    """Base API error."""


@dataclass(slots=True)
class JinoCredentials:
    login: str
    password: str


@dataclass(slots=True)
class NightscoutAccount:
    login: str
    password: str


def day(n: int) -> str:
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return "дней"
    last = n % 10
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


def time_to_pay(name: str, due_date: str, service_name: str = "Jino") -> tuple[str, int]:
    due = datetime.datetime.strptime(due_date, "%d.%m.%Y")
    now_ts = int(time.time())
    due_ts = int(due.timestamp()) + 10800
    days_left = (due_ts - now_ts) // 86400
    dword = day(days_left)

    if days_left == 0:
        msg = f"Сегодня срок оплаты {service_name} - {name}!"
    elif 0 < days_left <= 5:
        msg = f"Через {days_left} {dword} нужно оплатить {service_name} - {name}!"
    elif days_left < 0:
        msg = f"Просрочена оплата {service_name} - {name}!!!"
    else:
        msg = f"Все в порядке! Оплачивать {service_name} - {name} нужно через {days_left} {dword}."

    return msg, days_left


def normalize_date(date_str: str | None) -> str | None:
    if not date_str:
        return None

    date_str = date_str.strip()

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d.%m.%Y")

    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", date_str):
        return date_str

    return None


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = value.replace("ё", "e")
    value = re.sub(r"[^a-z0-9а-я_-]+", "_", value, flags=re.IGNORECASE)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def parse_nightscout_accounts(raw: str | list[dict[str, Any]] | None) -> list[NightscoutAccount]:
    if not raw:
        return []

    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as err:
            raise BillingApiError("Invalid Nightscout accounts JSON") from err

    if not isinstance(data, list):
        raise BillingApiError("Nightscout accounts must be a list")

    accounts: list[NightscoutAccount] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise BillingApiError(f"Nightscout account #{idx + 1} must be an object")

        login = str(item.get("login") or "").strip()
        password = str(item.get("password") or "")

        if not login or not password:
            raise BillingApiError("Nightscout account must include login and password")

        accounts.append(NightscoutAccount(login=login, password=password))

    return accounts


class JinoDomainsClient:
    LOGIN_PAGE = "https://auth.jino.ru/login/"
    GRAPHQL_URL = "https://graphql.jino.ru/user/"
    REFERER = "https://cp.jino.ru/domains/"

    LIST_DOMAINS_QUERY = """
    query ListDomains($first: Int, $after: String, $sort: [UserDomainSortEnum!]) {
      me {
        domain(first: $first, after: $after, sort: $sort) {
          edges {
            node {
              id
              domain
              rawDomain
            }
            cursor
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    }
    """

    DOMAIN_INFO_QUERY = """
    query DomainAdditionalInfo ($id: ID!) {
      node(id: $id) {
        ... on UserDomain {
          id
          domain
          renewal {
            autorenewalEnabled
            expireDate
            isExpired
            expiring
            renewalCost
            renewalAvailable
            canBeRenewedFromBalance
          }
          whoisData {
            expireDate
          }
        }
      }
    }
    """

    BALANCE_QUERY = """
    query GetBalance {
      me {
        funds
        realFunds
        bonusFunds
        billingData {
          paymentsCount
          expirationDays
          expirationDate
          expirationLabel
          minPayment
          minPersonPayment
          minOrgPayment
          maxPayment
          autoinvoiceEnabled
        }
      }
    }
    """

    def __init__(self, credentials: JinoCredentials, timeout: int = 30) -> None:
        self._credentials = credentials
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/147.0.0.0 Safari/537.36"
                )
            }
        )

    @staticmethod
    def _extract_csrf(html_text: str) -> str | None:
        patterns = [
            r"myv\.csrftoken\s*=\s*'([^']+)'",
            r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _is_retryable_error(err: Exception) -> bool:
        if isinstance(err, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True

        if isinstance(err, requests.exceptions.HTTPError):
            response = err.response
            if response is None:
                return False
            return response.status_code in (500, 502, 503, 504)

        return False

    def authenticate(self) -> None:
        response = self._session.get(self.LOGIN_PAGE, timeout=self._timeout)
        response.raise_for_status()

        csrf = self._extract_csrf(response.text)
        if not csrf:
            raise BillingApiError("Jino CSRF token not found")

        response = self._session.post(
            self.LOGIN_PAGE,
            data={
                "login": self._credentials.login,
                "password": self._credentials.password,
                "csrfmiddlewaretoken": csrf,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://auth.jino.ru",
                "Referer": self.LOGIN_PAGE,
            },
            allow_redirects=True,
            timeout=self._timeout,
        )
        response.raise_for_status()

    def _build_headers(self) -> dict[str, str]:
        token = self._session.cookies.get("auth._token.keycloak")
        if not token:
            raise BillingApiError("Jino bearer token not found in cookies")

        return {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Origin": "https://cp.jino.ru",
            "Referer": self.REFERER,
        }

    def _gql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "variables": variables or {},
        }
        if operation_name:
            payload["operationName"] = operation_name

        last_error: Exception | None = None

        for attempt in range(3):
            try:
                response = self._session.post(
                    self.GRAPHQL_URL,
                    headers=self._build_headers(),
                    json=payload,
                    timeout=self._timeout,
                )
                response.raise_for_status()

                data = response.json()
                if data.get("errors"):
                    raise BillingApiError(json.dumps(data["errors"], ensure_ascii=False))

                return data

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as err:
                last_error = err

                if not self._is_retryable_error(err):
                    raise

                if attempt < 2:
                    time.sleep(attempt + 1)

        if last_error is not None:
            raise last_error

        raise BillingApiError("Jino GraphQL request failed without details")

    def get_balance_info(self) -> dict[str, Any]:
        result = self._gql(self.BALANCE_QUERY, operation_name="GetBalance")
        me = result["data"]["me"]
        billing = me.get("billingData") or {}

        expire_date = normalize_date(billing.get("expirationDate"))
        message = None
        days_left = None
        if expire_date:
            message, days_left = time_to_pay("balance", expire_date, "Jino")

        return {
            "funds": me.get("funds"),
            "real_funds": me.get("realFunds"),
            "bonus_funds": me.get("bonusFunds"),
            "payments_count": billing.get("paymentsCount"),
            "expiration_days": billing.get("expirationDays"),
            "expiration_date": expire_date,
            "expiration_label": billing.get("expirationLabel"),
            "min_payment": billing.get("minPayment"),
            "min_person_payment": billing.get("minPersonPayment"),
            "min_org_payment": billing.get("minOrgPayment"),
            "max_payment": billing.get("maxPayment"),
            "autoinvoice_enabled": billing.get("autoinvoiceEnabled"),
            "days_left": days_left,
            "message": message,
        }

    def get_domains(self) -> list[dict[str, Any]]:
        result = self._gql(
            self.LIST_DOMAINS_QUERY,
            variables={"first": 100, "sort": ["DOMAIN_ASC"]},
            operation_name="ListDomains",
        )

        edges = result["data"]["me"]["domain"]["edges"]
        domains: list[dict[str, Any]] = []

        for edge in edges:
            node = edge["node"]
            domain_id = node["id"]

            info = self._gql(
                self.DOMAIN_INFO_QUERY,
                variables={"id": domain_id},
                operation_name="DomainAdditionalInfo",
            )["data"]["node"]

            renewal = info.get("renewal") or {}
            whois = info.get("whoisData") or {}
            expire_date = normalize_date(renewal.get("expireDate") or whois.get("expireDate"))

            message = None
            days_left = None
            if expire_date:
                message, days_left = time_to_pay(info["domain"], expire_date, "Jino")

            domains.append(
                {
                    "domain": info["domain"],
                    "expire_date": expire_date,
                    "autorenewal_enabled": renewal.get("autorenewalEnabled"),
                    "is_expired": renewal.get("isExpired"),
                    "expiring": renewal.get("expiring"),
                    "renewal_cost": renewal.get("renewalCost"),
                    "renewal_available": renewal.get("renewalAvailable"),
                    "can_be_renewed_from_balance": renewal.get("canBeRenewedFromBalance"),
                    "days_left": days_left,
                    "message": message,
                }
            )

        return domains

    def get_all(self) -> dict[str, Any]:
        return {
            "balance": self.get_balance_info(),
            "domains": self.get_domains(),
        }


class NightscoutEasyClient:
    CP_URL = "https://cp.nightscout-easy.ru/"
    AUTH_BASE = "https://auth.nightscout-easy.ru"
    NIGHTSCOUT_GRAPHQL_URL = "https://graphql.nightscout-easy.ru/user/"

    NIGHTSCOUT_ACCOUNTS_QUERY = """
    query NightscoutAccounts {
      me {
        nightscoutAccounts {
          nodes {
            id
            name
            blocked
            paidTill
            __typename
          }
          __typename
        }
        __typename
      }
    }
    """

    NIGHTSCOUT_PRICES_QUERY = """
    query NightscoutPrices($noPromo: Boolean, $nightscoutId: ID) {
      nightscout {
        prices(noPromo: $noPromo, nightscoutId: $nightscoutId) {
          months
          price
          basePrice
          promo
          __typename
        }
        __typename
      }
    }
    """

    def __init__(self, account: NightscoutAccount, timeout: int = 30) -> None:
        self._account = account
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/147.0.0.0 Safari/537.36"
                )
            }
        )
        self._accounts_cache: list[dict[str, Any]] | None = None
        self._current_account_cache: dict[str, Any] | None = None
        self._prices_cache: dict[str, list[dict[str, Any]]] = {}
        self._current_cp_url: str | None = None

    def _get_login_page(self) -> tuple[str, str]:
        response = self._session.get(self.CP_URL, timeout=self._timeout, allow_redirects=True)
        response.raise_for_status()
        return response.url, response.text

    @staticmethod
    def _extract_form_action(html_text: str, base_url: str) -> str:
        match = re.search(
            r'<form[^>]*action=["\']([^"\']+)["\']',
            html_text,
            re.IGNORECASE | re.DOTALL,
        )
        action = match.group(1).strip() if match else base_url

        if action.startswith("/"):
            return urljoin(NightscoutEasyClient.AUTH_BASE, action)
        if action.startswith("?"):
            return base_url.split("?", 1)[0] + action
        if not action.startswith("http"):
            return urljoin(NightscoutEasyClient.AUTH_BASE + "/", action.lstrip("/"))

        return action

    @staticmethod
    def _extract_inputs_from_html(html_text: str) -> dict[str, str]:
        payload: dict[str, str] = {}

        input_pattern = re.compile(r"<input\b([^>]*)>", re.IGNORECASE | re.DOTALL)
        attr_pattern = re.compile(
            r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*["\']([^"\']*)["\']',
            re.IGNORECASE | re.DOTALL,
        )

        for input_match in input_pattern.finditer(html_text):
            attrs_raw = input_match.group(1)
            attrs = {k.lower(): html.unescape(v) for k, v in attr_pattern.findall(attrs_raw)}

            name = attrs.get("name")
            if not name:
                continue

            payload[name] = attrs.get("value", "")

        return payload

    @staticmethod
    def _extract_data_form_payload(html_text: str) -> dict[str, str]:
        payload: dict[str, str] = {}

        match = re.search(
            r'data-form=["\']([^"\']+)["\']',
            html_text,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return payload

        raw_data_form = html.unescape(match.group(1))

        try:
            data_form = json.loads(raw_data_form)
        except json.JSONDecodeError:
            return payload

        for field in data_form.get("fields", []):
            name = field.get("name")
            if not name:
                continue
            payload[name] = field.get("value") if field.get("value") is not None else ""

        return payload

    def authenticate(self) -> None:
        login_url, html_text = self._get_login_page()

        action = self._extract_form_action(html_text, login_url)
        payload = self._extract_inputs_from_html(html_text)
        payload.update(self._extract_data_form_payload(html_text))

        if "login" in payload:
            payload["login"] = self._account.login
        elif "username" in payload:
            payload["username"] = self._account.login
        elif "email" in payload:
            payload["email"] = self._account.login
        else:
            raise BillingApiError(f"Nightscout login field not found. Keys: {list(payload.keys())}")

        if "password" in payload:
            payload["password"] = self._account.password
        elif "passwd" in payload:
            payload["passwd"] = self._account.password
        else:
            raise BillingApiError(f"Nightscout password field not found. Keys: {list(payload.keys())}")

        csrftoken = self._session.cookies.get("csrftoken")
        if csrftoken and "csrfmiddlewaretoken" not in payload:
            payload["csrfmiddlewaretoken"] = csrftoken

        response = self._session.post(
            action,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.AUTH_BASE,
                "Referer": login_url,
            },
            timeout=self._timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        self._current_cp_url = response.url

        try:
            self._get_bearer_token()
        except BillingApiError as err:
            raise BillingApiError(
                f"Nightscout auth succeeded without bearer token. Final URL: {self._current_cp_url}"
            ) from err

    @staticmethod
    def extract_account_slug(url: str) -> str | None:
        match = re.search(r"/ru/([a-zA-Z0-9]+)/?", url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def make_nightscout_graphql_id(slug: str) -> str:
        raw = f"NightscoutAccount:{slug}"
        return base64.b64encode(raw.encode("utf-8")).decode("ascii")

    @staticmethod
    def normalize_api_date(date_str: str | None) -> str | None:
        if not date_str:
            return None

        date_str = date_str.strip()

        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.datetime.strptime(date_str, fmt)
                return dt.strftime("%d.%m.%Y")
            except ValueError:
                continue

        return None

    def _get_bearer_token(self) -> str:
        possible_cookie_names = [
            "auth._token.keycloak",
            "auth._token",
            "token",
            "access_token",
        ]

        for name in possible_cookie_names:
            token = self._session.cookies.get(name)
            if token:
                return token

        for cookie in self._session.cookies:
            cname = cookie.name.lower()
            if "token" in cname or "keycloak" in cname:
                return cookie.value

        raise BillingApiError("Nightscout bearer token not found in session cookies")

    def _gql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        token = self._get_bearer_token()

        payload: dict[str, Any] = {
            "query": query,
            "variables": variables or {},
        }
        if operation_name:
            payload["operationName"] = operation_name

        response = self._session.post(
            self.NIGHTSCOUT_GRAPHQL_URL,
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "Origin": "https://cp.nightscout-easy.ru",
                "Referer": "https://cp.nightscout-easy.ru/",
            },
            json=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()

        data = response.json()
        if data.get("errors"):
            raise BillingApiError(json.dumps(data["errors"], ensure_ascii=False))

        return data

    def get_accounts(self) -> list[dict[str, Any]]:
        if self._accounts_cache is not None:
            return self._accounts_cache

        result = self._gql(
            self.NIGHTSCOUT_ACCOUNTS_QUERY,
            variables={},
            operation_name="NightscoutAccounts",
        )
        self._accounts_cache = result["data"]["me"]["nightscoutAccounts"]["nodes"]
        return self._accounts_cache

    def get_current_account(self) -> dict[str, Any]:
        if self._current_account_cache is not None:
            return self._current_account_cache

        accounts = self.get_accounts()
        if not accounts:
            raise BillingApiError("Nightscout accounts not found")

        if len(accounts) == 1:
            self._current_account_cache = accounts[0]
            return self._current_account_cache

        slug = self.extract_account_slug(self._current_cp_url or "")
        if slug:
            expected_id = self.make_nightscout_graphql_id(slug)
            for account in accounts:
                if account.get("id") == expected_id:
                    self._current_account_cache = account
                    return self._current_account_cache

        for account in accounts:
            account_name = str(account.get("name") or "").lower()
            if self._account.login.lower() in account_name:
                self._current_account_cache = account
                return self._current_account_cache

        self._current_account_cache = accounts[0]
        return self._current_account_cache

    def get_prices(self, nightscout_id: str) -> list[dict[str, Any]]:
        if nightscout_id in self._prices_cache:
            return self._prices_cache[nightscout_id]

        result = self._gql(
            self.NIGHTSCOUT_PRICES_QUERY,
            variables={
                "noPromo": True,
                "nightscoutId": nightscout_id,
            },
            operation_name="NightscoutPrices",
        )

        prices = result["data"]["nightscout"]["prices"]
        self._prices_cache[nightscout_id] = prices
        return prices

    def get_info(self) -> dict[str, Any]:
        account = self.get_current_account()

        name = account.get("name")
        blocked = account.get("blocked")
        expire_date = self.normalize_api_date(account.get("paidTill"))
        nightscout_id = account.get("id")

        year_cost = None
        if nightscout_id:
            prices = self.get_prices(nightscout_id)
            for item in prices:
                if item.get("months") == 12:
                    year_cost = item.get("price")
                    break

        message = None
        days_left = None
        if name and expire_date:
            message, days_left = time_to_pay(name, expire_date, "Nightscout")

        return {
            "name": name,
            "blocked": blocked,
            "expire_date": expire_date,
            "year_cost": year_cost,
            "days_left": days_left,
            "message": message,
        }
