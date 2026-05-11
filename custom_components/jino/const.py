from __future__ import annotations

DOMAIN = "jino"

INTEGRATION_NAME = "Billing Monitor"
MANUFACTURER = "Jino"

PLATFORMS = ["sensor", "button"]

DATA_COORDINATOR = "coordinator"
DATA_UNSUB_OPTIONS = "unsub_options"

DEFAULT_SCAN_INTERVAL_MINUTES = 60

CONF_JINO_LOGIN = "jino_login"
CONF_JINO_PASSWORD = "jino_password"
CONF_NIGHTSCOUT_ACCOUNTS = "nightscout_accounts"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

ATTR_DUE_DATE = "due_date"
ATTR_DAYS_LEFT = "days_left"
ATTR_MESSAGE = "message"

ATTR_REAL_FUNDS = "real_funds"
ATTR_BONUS_FUNDS = "bonus_funds"
ATTR_PAYMENTS_COUNT = "payments_count"
ATTR_EXPIRATION_DAYS = "expiration_days"
ATTR_EXPIRATION_LABEL = "expiration_label"
ATTR_MIN_PAYMENT = "min_payment"
ATTR_MIN_PERSON_PAYMENT = "min_person_payment"
ATTR_MIN_ORG_PAYMENT = "min_org_payment"
ATTR_MAX_PAYMENT = "max_payment"
ATTR_AUTOINVOICE_ENABLED = "autoinvoice_enabled"

ATTR_AUTORENEWAL = "autorenewal_enabled"
ATTR_IS_EXPIRED = "is_expired"
ATTR_EXPIRING = "expiring"
ATTR_RENEWAL_COST = "renewal_cost"
ATTR_RENEWAL_AVAILABLE = "renewal_available"
ATTR_CAN_BE_RENEWED_FROM_BALANCE = "can_be_renewed_from_balance"

ATTR_YEAR_COST = "year_cost"
ATTR_BLOCKED = "blocked"