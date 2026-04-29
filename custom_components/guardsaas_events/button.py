import logging
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, GUARDSAAS_BASE_URL

_LOGGER = logging.getLogger(__name__)

CONTROLLER_LIST_ENDPOINTS = (
    "/equipment/controller/list/export",
    "/equipment/controller/api/list/",
)

OPEN_ENDPOINTS = (
    "/equipment/controller/api/{id}/open_door/{token}",
    "/equipment/cmd_opendoor/{id}/{token}",
)


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", str(value).lower()).strip("_")


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _login(session: requests.Session, username: str, password: str) -> None:
    login_page = session.get(f"{GUARDSAAS_BASE_URL}/login", timeout=20)
    login_page.raise_for_status()

    soup = BeautifulSoup(login_page.text, "html.parser")
    token_input = soup.find("input", {"name": "_csrf_token"})
    if token_input is None or not token_input.get("value"):
        raise RuntimeError("CSRF token not found")

    auth_data = {
        "_username": username,
        "_password": password,
        "_remember_me": "on",
        "_csrf_token": token_input["value"],
    }

    login_response = session.post(
        f"{GUARDSAAS_BASE_URL}/login_check",
        data=auth_data,
        timeout=20,
    )
    login_response.raise_for_status()

    if "logout" not in login_response.text and "/login" in login_response.url:
        raise RuntimeError("invalid_auth")


def _logout(session: requests.Session) -> None:
    try:
        session.get(f"{GUARDSAAS_BASE_URL}/logout", timeout=20)
    except Exception:
        pass


def _items_from_response(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "data", "controllers", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if data.get("id") is not None:
            return [data]
    return []


def _fetch_json_items(session: requests.Session, endpoints: tuple[str, ...], referer: str) -> list[dict[str, Any]]:
    last_error = None
    for endpoint in endpoints:
        url = f"{GUARDSAAS_BASE_URL}{endpoint}"
        try:
            response = session.get(
                url,
                timeout=20,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": referer,
                },
            )
            response.raise_for_status()
            if "/login" in response.url or "_csrf_token" in response.text:
                last_error = f"{endpoint}: redirected to login"
                continue
            data = response.json()
            items = _items_from_response(data)
            if items:
                return items
            last_error = f"{endpoint}: empty or unexpected response"
        except Exception as exc:
            last_error = f"{endpoint}: {exc}"
            _LOGGER.debug("GuardSaaS list attempt failed: %s", last_error)
    raise RuntimeError(last_error or "list is empty")


def _fetch_controller_list(session: requests.Session) -> list[dict[str, Any]]:
    """Fetch controllers from all known endpoints and merge records by id.

    /equipment/controller/list/export may not include token1 on some accounts,
    while /equipment/controller/api/list/ can include additional fields.
    """
    merged: dict[str, dict[str, Any]] = {}
    last_error = None

    for endpoint in CONTROLLER_LIST_ENDPOINTS:
        url = f"{GUARDSAAS_BASE_URL}{endpoint}"
        try:
            response = session.get(
                url,
                timeout=20,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{GUARDSAAS_BASE_URL}/equipment/controller/list",
                },
            )
            response.raise_for_status()

            if "/login" in response.url or "_csrf_token" in response.text:
                last_error = f"{endpoint}: redirected to login"
                continue

            items = _items_from_response(response.json())
            for item in items:
                controller_id = _controller_id(item)
                if not controller_id:
                    continue
                if controller_id not in merged:
                    merged[controller_id] = {}
                # Later endpoints can add token1 or other missing fields.
                merged[controller_id].update(item)

            _LOGGER.debug(
                "GuardSaaS controller endpoint %s returned %s items",
                endpoint,
                len(items),
            )
        except Exception as exc:
            last_error = f"{endpoint}: {exc}"
            _LOGGER.debug("GuardSaaS controller list attempt failed: %s", last_error)

    if merged:
        return list(merged.values())

    raise RuntimeError(last_error or "controller list is empty")


def _fetch_object_list(session: requests.Session) -> list[dict[str, Any]]:
    return _fetch_json_items(
        session,
        ("/object/list/export",),
        f"{GUARDSAAS_BASE_URL}/object/list",
    )


def _controller_field(controller: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in controller and controller.get(name) not in (None, ""):
            return controller.get(name)
    return None


def _controller_id(controller: dict[str, Any]) -> str | None:
    value = _controller_field(controller, "id", "controller_id", "controllerid", "controllerId")
    return str(value) if value not in (None, "") else None


def _controller_token(controller: dict[str, Any]) -> str | None:
    value = _controller_field(controller, "token1", "token_1", "open_token", "opendoor_token", "token")
    return str(value) if value not in (None, "") else None


def _controller_label(controller: dict[str, Any]) -> str:
    controller_id = _controller_id(controller)
    name = _controller_field(controller, "name", "description", "title", "serial", "number")
    serial = _controller_field(controller, "serial", "serial_number", "serialNumber", "sn")
    parts = []
    if controller_id:
        parts.append(f"id={controller_id}")
    if name:
        parts.append(str(name))
    if serial and str(serial) != str(name):
        parts.append(f"serial={serial}")
    return ", ".join(parts) or str(controller)


def _walk_values(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    elif value not in (None, ""):
        yield str(value)


def _phone_candidates(value: Any) -> set[str]:
    candidates: set[str] = set()
    for text in _walk_values(value):
        for raw in re.findall(r"(?:\+?7|8)?[\s\-()]*(?:\d[\s\-()]*){10,11}", text):
            digits = re.sub(r"\D+", "", raw)
            if len(digits) == 11 and digits.startswith("8"):
                digits = "7" + digits[1:]
            if len(digits) >= 10:
                candidates.add(digits)
                candidates.add(digits[-10:])
    return candidates


def _object_record(objects: list[dict[str, Any]], object_id: str, object_name: str) -> dict[str, Any]:
    for obj in objects:
        if str(obj.get("id")) == str(object_id):
            return obj
    for obj in objects:
        if _norm(obj.get("name")) == _norm(object_name):
            return obj
    return {"id": object_id, "name": object_name}


def _object_controller_ids(obj: dict[str, Any]) -> set[str]:
    """Return controller ids explicitly linked to an object.

    GuardSaaS /object/list/export can return:
        {"id": 799, "name": "...", "controllers": [946]}
    This is the most reliable mapping source.
    """
    result: set[str] = set()

    for key in ("controllers", "controller_ids", "controllerIds", "controller_id", "controllerId"):
        value = obj.get(key)

        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    controller_id = _controller_id(item)
                    if controller_id:
                        result.add(controller_id)
                elif item not in (None, ""):
                    result.add(str(item))

        elif isinstance(value, dict):
            controller_id = _controller_id(value)
            if controller_id:
                result.add(controller_id)

        elif value not in (None, ""):
            result.add(str(value))

    return result


def _controller_matches_object(
    controller: dict[str, Any], object_id: str, object_name: str, obj: dict[str, Any]
) -> bool:
    object_phones = _phone_candidates(obj)
    controller_phones = _phone_candidates(controller)
    if object_phones and controller_phones and object_phones.intersection(controller_phones):
        return True

    object_id_norm = _norm(object_id)
    for field in (
        "object_id", "objectid", "objectId", "object", "objectid_id",
        "place_id", "placeid", "building_id", "buildingid",
    ):
        value = controller.get(field)
        if value is not None and _norm(value) == object_id_norm:
            return True

    object_name_norm = _norm(object_name)
    for field in ("object_name", "objectName", "object", "description", "title", "address"):
        value_norm = _norm(controller.get(field))
        if value_norm and object_name_norm and value_norm == object_name_norm:
            return True

    return False


def _find_controller(
    controllers: list[dict[str, Any]], objects: list[dict[str, Any]], object_id: str, object_name: str
) -> dict[str, Any]:
    obj = _object_record(objects, object_id, object_name)

    # Best mapping source: /object/list/export contains explicit controller ids.
    linked_controller_ids = _object_controller_ids(obj)
    if linked_controller_ids:
        matched_by_object = [
            controller
            for controller in controllers
            if _controller_id(controller) in linked_controller_ids
        ]
        if len(matched_by_object) == 1:
            _LOGGER.info(
                "GuardSaaS controller auto-matched by object.controllers for %s (%s): %s",
                object_name,
                object_id,
                _controller_label(matched_by_object[0]),
            )
            return matched_by_object[0]
        if len(matched_by_object) > 1:
            raise RuntimeError(
                "Найдено несколько контроллеров из object.controllers для объекта "
                f"{object_name} ({object_id}): "
                + "; ".join(_controller_label(item) for item in matched_by_object)
            )

    phone_matches = []
    object_phones = _phone_candidates(obj)
    if object_phones:
        for controller in controllers:
            if object_phones.intersection(_phone_candidates(controller)):
                phone_matches.append(controller)
        if len(phone_matches) == 1:
            _LOGGER.info(
                "GuardSaaS controller auto-matched by phone for %s (%s): %s",
                object_name,
                object_id,
                _controller_label(phone_matches[0]),
            )
            return phone_matches[0]
        if len(phone_matches) > 1:
            raise RuntimeError(
                "Найдено несколько контроллеров по номеру телефона для объекта "
                f"{object_name} ({object_id}): "
                + "; ".join(_controller_label(item) for item in phone_matches)
            )

    matched = [
        controller
        for controller in controllers
        if _controller_matches_object(controller, object_id, object_name, obj)
    ]
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        raise RuntimeError(
            "Найдено несколько контроллеров для объекта "
            f"{object_name} ({object_id}): "
            + "; ".join(_controller_label(item) for item in matched)
        )
    if len(controllers) == 1:
        return controllers[0]

    object_phone_text = ", ".join(sorted(object_phones)) if object_phones else "не найден"
    linked_text = ", ".join(sorted(_object_controller_ids(obj))) or "не найдены"
    raise RuntimeError(
        "Не удалось автоматически сопоставить объект Home Assistant с контроллером GuardSaaS. "
        f"Объект: {object_name} ({object_id}). "
        f"controllers в объекте: {linked_text}. "
        f"Телефон в объекте: {object_phone_text}. "
        "Доступные контроллеры: "
        + "; ".join(_controller_label(item) for item in controllers[:10])
    )


def _fetch_open_token(session: requests.Session) -> str:
    """Fetch global token1 used by GuardSaaS open_door endpoint.

    On this GuardSaaS account /equipment/controller/api/list returns:
        {"token1": "..."}
    while /equipment/controller/list/export returns controller records without token1.
    """
    urls = (
        f"{GUARDSAAS_BASE_URL}/equipment/controller/api/list",
        f"{GUARDSAAS_BASE_URL}/equipment/controller/api/list/",
    )

    last_error = None

    for url in urls:
        try:
            response = session.get(
                url,
                timeout=20,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{GUARDSAAS_BASE_URL}/equipment/controller/list",
                },
            )
            response.raise_for_status()

            if "/login" in response.url or "_csrf_token" in response.text:
                last_error = f"{url}: redirected to login"
                continue

            data = response.json()

            if isinstance(data, dict):
                token = (
                    data.get("token1")
                    or data.get("token")
                    or data.get("open_token")
                    or data.get("opendoor_token")
                )
                if token:
                    return str(token)

                # Defensive fallback: token can be nested.
                for value in data.values():
                    if isinstance(value, dict):
                        token = (
                            value.get("token1")
                            or value.get("token")
                            or value.get("open_token")
                            or value.get("opendoor_token")
                        )
                        if token:
                            return str(token)

            last_error = f"{url}: token1 not found in response"
        except Exception as exc:
            last_error = f"{url}: {exc}"
            _LOGGER.debug("GuardSaaS token fetch failed: %s", last_error)

    raise RuntimeError(f"Не удалось получить token1 для открытия: {last_error}")


def _extract_controller_open_data(controller: dict[str, Any]) -> tuple[str, str | None]:
    controller_id = _controller_id(controller)
    token = _controller_token(controller)
    if not controller_id:
        raise RuntimeError("В данных контроллера нет id")
    return controller_id, token


def _mask_token(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _get_mapped_controller(config: dict[str, Any], object_id: str) -> dict[str, Any] | None:
    mapping = config.get("object_controller_map") or {}
    mapped = mapping.get(str(object_id)) or mapping.get(object_id)
    if not isinstance(mapped, dict):
        return None
    controller_id = mapped.get("controller_id") or mapped.get("id")
    token = mapped.get("token1") or mapped.get("token")
    if controller_id and token:
        return {
            "id": str(controller_id),
            "token1": str(token),
            "name": mapped.get("controller_name") or mapped.get("name") or str(controller_id),
            "serial": mapped.get("serial"),
        }
    return None


def open_guardsaas_object(config: dict[str, Any], object_id: str, object_name: str) -> None:
    session = requests.Session()
    try:
        _login(session, config["_username"], config["_password"])

        controller = _get_mapped_controller(config, object_id)
        if controller is None:
            controllers = _fetch_controller_list(session)
            objects = _fetch_object_list(session)
            controller = _find_controller(controllers, objects, object_id, object_name)

        controller_id, token = _extract_controller_open_data(controller)
        if not token:
            token = _fetch_open_token(session)

        endpoints = (
            ("POST", f"/equipment/controller/api/{controller_id}/open_door/{token}"),
            ("GET", f"/equipment/controller/api/{controller_id}/open_door/{token}"),
            ("POST", f"/equipment/cmd_opendoor/{controller_id}/{token}"),
            ("GET", f"/equipment/cmd_opendoor/{controller_id}/{token}"),
        )

        last_error = None

        for method, endpoint in endpoints:
            url = f"{GUARDSAAS_BASE_URL}{endpoint}"
            safe_url = _mask_token(url, token)

            try:
                headers = {
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{GUARDSAAS_BASE_URL}/equipment/controller/list",
                }

                if method == "POST":
                    response = session.post(url, timeout=20, headers=headers)
                else:
                    response = session.get(url, timeout=20, headers=headers)

                text = response.text[:500].replace("\n", " ")
                safe_text = _mask_token(text, token)

                _LOGGER.debug(
                    "GuardSaaS official open attempt: %s %s status=%s response=%s",
                    method,
                    safe_url,
                    response.status_code,
                    safe_text,
                )

                if response.status_code in (200, 201, 202, 204):
                    if "/login" in response.url or "_csrf_token" in response.text:
                        last_error = f"{method} {safe_url}: redirected to login"
                        continue

                    try:
                        data = response.json()
                    except Exception:
                        data = {}

                    message = ""
                    if isinstance(data, dict):
                        message = str(data.get("message") or data.get("error") or "")

                    if message and message != "Command OK":
                        last_error = f"{method} {safe_url}: {safe_text}"
                        continue

                    _LOGGER.info(
                        "GuardSaaS open command sent for object %s (%s) via controller %s",
                        object_name,
                        object_id,
                        controller_id,
                    )
                    return

                last_error = f"{method} {safe_url}: HTTP {response.status_code}; {safe_text}"
            except Exception as exc:
                last_error = f"{method} {safe_url}: {exc}"
                _LOGGER.debug("GuardSaaS official open attempt failed: %s", last_error)

        raise RuntimeError(last_error or "No GuardSaaS official open endpoint succeeded")
    finally:
        _logout(session)


async def async_setup_entry(hass, entry, async_add_entities):
    config = {**entry.data, **(entry.options or {})}
    target_objects = config.get("target_objects")
    object_ids = config.get("object_ids")

    if not target_objects:
        target_objects = [config.get("target_object")]
    if not object_ids:
        object_ids = [config.get("object_id") or config.get("target_object")]

    entities = []
    for target_object, object_id in zip(target_objects, object_ids):
        if not target_object or object_id is None:
            continue
        entities.append(GuardSaaSOpenObjectButton(hass, config, str(object_id), target_object))
    async_add_entities(entities)


class GuardSaaSOpenObjectButton(ButtonEntity):
    _attr_translation_key = "open_object"

    def __init__(self, hass, config, object_id: str, object_name: str):
        self.hass = hass
        self._config = config
        self._object_id = object_id
        self._object_name = object_name
        self._attr_name = f"GuardSaaS - Открыть {object_name}"
        self._attr_unique_id = f"guardsaas_open_{_slug(object_id)}"
        self._attr_icon = "mdi:gate-open"

    async def async_press(self) -> None:
        try:
            await self.hass.async_add_executor_job(
                open_guardsaas_object,
                self._config,
                self._object_id,
                self._object_name,
            )
        except Exception as exc:
            _LOGGER.error(
                "Не удалось открыть GuardSaaS объект %s (%s): %s",
                self._object_name,
                self._object_id,
                exc,
            )
            raise HomeAssistantError(
                f"Не удалось открыть GuardSaaS объект {self._object_name}: {exc}"
            ) from exc

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"guardsaas_{_slug(self._object_id)}")},
            "name": f"GuardSaaS - {self._object_name}",
            "manufacturer": "GuardSaaS",
            "model": "Barrier/Gate",
        }
