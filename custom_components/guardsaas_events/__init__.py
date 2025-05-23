from .const import DOMAIN
import voluptuous as vol

async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    if "entities" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["entities"] = []

    async def async_reload_sensor_service(call):
        entity_id = call.data.get("entity_id")
        found = False
        for entity in hass.data[DOMAIN]["entities"]:
            if entity_id is None or entity.entity_id == entity_id:
                await entity.async_update_ha_state(force_refresh=True)
                found = True
        if not found and entity_id:
            print(f"[guardsaas_events] Entity '{entity_id}' not found for reload")

    hass.services.async_register(
        DOMAIN,
        "reload_sensor",
        async_reload_sensor_service,
        schema=vol.Schema({
            vol.Optional("entity_id"): str,
        }),
    )
    return True

async def async_unload_entry(hass, entry):
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    if DOMAIN in hass.data:
        hass.data[DOMAIN].pop("entities", None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok
