"""Config flow and options flow for Stopfinder."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    API_BASE,
    CONF_EARLY_HOUR,
    CONF_HALFDAY_HOUR,
    CONF_MINUTES_AFTER_HOME_DROPOFF,
    CONF_MINUTES_AFTER_SCHOOL_DROPOFF,
    CONF_MINUTES_BEFORE_HOME_PICKUP,
    CONF_MINUTES_BEFORE_SCHOOL_PICKUP,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_USER_AGENT,
    CONF_USERNAME,
    CONF_ZONE_NEIGHBORHOOD,
    CONF_ZONE_SCHOOL,
    DEFAULT_MINUTES_AFTER_HOME_DROPOFF,
    DEFAULT_MINUTES_AFTER_SCHOOL_DROPOFF,
    DEFAULT_MINUTES_BEFORE_HOME_PICKUP,
    DEFAULT_MINUTES_BEFORE_SCHOOL_PICKUP,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_USER_AGENT,
    DOMAIN,
    EARLY_THRESHOLD_HOUR,
    HALFDAY_THRESHOLD_HOUR,
)

_LOGGER = logging.getLogger(__name__)

_WINDOW_SELECTOR = selector.NumberSelectorConfig(
    min=0, max=60, step=5, unit_of_measurement="min",
    mode=selector.NumberSelectorMode.SLIDER,
)


# ---------------------------------------------------------------------------
# Credential validation
# ---------------------------------------------------------------------------

class InvalidAuth(Exception):
    pass


class CannotConnect(Exception):
    pass


async def _test_credentials(
    hass: HomeAssistant,
    username: str,
    password: str,
    user_agent: str,
) -> None:
    """Validate credentials against the Stopfinder API (raises on failure)."""
    session = async_get_clientsession(hass)
    headers = {"User-Agent": user_agent, "Content-Type": "application/json"}

    try:
        async with session.post(
            f"{API_BASE}/tokens",
            json={
                "deviceId": "",
                "grantType": "password",
                "username": username,
                "password": password,
                "rfApiVersion": "1.1",
            },
            headers=headers,
        ) as resp:
            if resp.status == 401:
                raise InvalidAuth
            resp.raise_for_status()
            token_data = await resp.json()
    except InvalidAuth:
        raise
    except Exception as err:
        raise CannotConnect from err

    if not token_data.get("token"):
        raise CannotConnect


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class StopfinderConfigFlow(ConfigFlow, domain=DOMAIN):
    """Multi-step setup wizard: credentials → tracking preferences."""

    VERSION = 1
    _credentials: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}

        if user_input is not None:
            username   = user_input[CONF_USERNAME].strip()
            password   = user_input[CONF_PASSWORD]
            user_agent = (user_input.get(CONF_USER_AGENT) or DEFAULT_USER_AGENT).strip()

            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            try:
                await _test_credentials(self.hass, username, password, user_agent)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Stopfinder credentials")
                errors["base"] = "unknown"
            else:
                self._credentials = {
                    CONF_USERNAME:   username,
                    CONF_PASSWORD:   password,
                    CONF_USER_AGENT: user_agent,
                }
                return await self.async_step_preferences()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.EMAIL,
                        autocomplete="email",
                    )
                ),
                vol.Required(CONF_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    )
                ),
                vol.Optional(CONF_USER_AGENT, default=DEFAULT_USER_AGENT): selector.TextSelector(),
            }),
            errors=errors,
        )

    async def async_step_preferences(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}

        if user_input is not None:
            halfday_h = int(user_input[CONF_HALFDAY_HOUR])
            early_h   = int(user_input[CONF_EARLY_HOUR])
            if early_h <= halfday_h:
                errors["base"] = "invalid_thresholds"
            else:
                data = {
                    **self._credentials,
                    CONF_POLL_INTERVAL:                user_input[CONF_POLL_INTERVAL],
                    CONF_MINUTES_BEFORE_HOME_PICKUP:   user_input[CONF_MINUTES_BEFORE_HOME_PICKUP],
                    CONF_MINUTES_AFTER_SCHOOL_DROPOFF: user_input[CONF_MINUTES_AFTER_SCHOOL_DROPOFF],
                    CONF_MINUTES_BEFORE_SCHOOL_PICKUP: user_input[CONF_MINUTES_BEFORE_SCHOOL_PICKUP],
                    CONF_MINUTES_AFTER_HOME_DROPOFF:   user_input[CONF_MINUTES_AFTER_HOME_DROPOFF],
                    CONF_HALFDAY_HOUR:                 halfday_h,
                    CONF_EARLY_HOUR:                   early_h,
                }
                return self.async_create_entry(title="Stopfinder", data=data)

        return self.async_show_form(
            step_id="preferences",
            data_schema=vol.Schema({
                vol.Required(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5, max=300, step=5,
                        unit_of_measurement="seconds",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Required(CONF_MINUTES_BEFORE_HOME_PICKUP,
                             default=DEFAULT_MINUTES_BEFORE_HOME_PICKUP):
                    selector.NumberSelector(_WINDOW_SELECTOR),
                vol.Required(CONF_MINUTES_AFTER_SCHOOL_DROPOFF,
                             default=DEFAULT_MINUTES_AFTER_SCHOOL_DROPOFF):
                    selector.NumberSelector(_WINDOW_SELECTOR),
                vol.Required(CONF_MINUTES_BEFORE_SCHOOL_PICKUP,
                             default=DEFAULT_MINUTES_BEFORE_SCHOOL_PICKUP):
                    selector.NumberSelector(_WINDOW_SELECTOR),
                vol.Required(CONF_MINUTES_AFTER_HOME_DROPOFF,
                             default=DEFAULT_MINUTES_AFTER_HOME_DROPOFF):
                    selector.NumberSelector(_WINDOW_SELECTOR),
                vol.Required(CONF_HALFDAY_HOUR, default=HALFDAY_THRESHOLD_HOUR):
                    selector.NumberSelector(selector.NumberSelectorConfig(
                        min=8, max=15, step=1,
                        unit_of_measurement="h (24h)",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )),
                vol.Required(CONF_EARLY_HOUR, default=EARLY_THRESHOLD_HOUR):
                    selector.NumberSelector(selector.NumberSelectorConfig(
                        min=9, max=16, step=1,
                        unit_of_measurement="h (24h)",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )),
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return StopfinderOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

class StopfinderOptionsFlow(OptionsFlow):
    """Adjust settings post-setup without reinstalling."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}

        if user_input is not None:
            halfday_h = int(user_input[CONF_HALFDAY_HOUR])
            early_h   = int(user_input[CONF_EARLY_HOUR])
            if early_h <= halfday_h:
                errors["base"] = "invalid_thresholds"
            else:
                if not user_input.get(CONF_ZONE_NEIGHBORHOOD):
                    user_input.pop(CONF_ZONE_NEIGHBORHOOD, None)
                if not user_input.get(CONF_ZONE_SCHOOL):
                    user_input.pop(CONF_ZONE_SCHOOL, None)
                return self.async_create_entry(title="", data=user_input)

        opts = self._config_entry.options
        data = self._config_entry.data

        def _get(key: str, legacy_key: str, default: Any) -> Any:
            return opts.get(key, data.get(key, opts.get(legacy_key, data.get(legacy_key, default))))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_POLL_INTERVAL,
                             default=opts.get(CONF_POLL_INTERVAL, data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))):
                    selector.NumberSelector(selector.NumberSelectorConfig(
                        min=5, max=300, step=5,
                        unit_of_measurement="seconds",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )),
                vol.Required(CONF_MINUTES_BEFORE_HOME_PICKUP,
                             default=_get(CONF_MINUTES_BEFORE_HOME_PICKUP,   "minutes_before", DEFAULT_MINUTES_BEFORE_HOME_PICKUP)):
                    selector.NumberSelector(_WINDOW_SELECTOR),
                vol.Required(CONF_MINUTES_AFTER_SCHOOL_DROPOFF,
                             default=_get(CONF_MINUTES_AFTER_SCHOOL_DROPOFF, "minutes_after",  DEFAULT_MINUTES_AFTER_SCHOOL_DROPOFF)):
                    selector.NumberSelector(_WINDOW_SELECTOR),
                vol.Required(CONF_MINUTES_BEFORE_SCHOOL_PICKUP,
                             default=_get(CONF_MINUTES_BEFORE_SCHOOL_PICKUP, "minutes_before", DEFAULT_MINUTES_BEFORE_SCHOOL_PICKUP)):
                    selector.NumberSelector(_WINDOW_SELECTOR),
                vol.Required(CONF_MINUTES_AFTER_HOME_DROPOFF,
                             default=_get(CONF_MINUTES_AFTER_HOME_DROPOFF,   "minutes_after",  DEFAULT_MINUTES_AFTER_HOME_DROPOFF)):
                    selector.NumberSelector(_WINDOW_SELECTOR),
                vol.Required(CONF_HALFDAY_HOUR,
                             default=opts.get(CONF_HALFDAY_HOUR, data.get(CONF_HALFDAY_HOUR, HALFDAY_THRESHOLD_HOUR))):
                    selector.NumberSelector(selector.NumberSelectorConfig(
                        min=8, max=15, step=1,
                        unit_of_measurement="h (24h)",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )),
                vol.Required(CONF_EARLY_HOUR,
                             default=opts.get(CONF_EARLY_HOUR, data.get(CONF_EARLY_HOUR, EARLY_THRESHOLD_HOUR))):
                    selector.NumberSelector(selector.NumberSelectorConfig(
                        min=9, max=16, step=1,
                        unit_of_measurement="h (24h)",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )),
                vol.Optional(CONF_ZONE_NEIGHBORHOOD,
                             description={"suggested_value": opts.get(CONF_ZONE_NEIGHBORHOOD)}):
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="zone", multiple=False)),
                vol.Optional(CONF_ZONE_SCHOOL,
                             description={"suggested_value": opts.get(CONF_ZONE_SCHOOL)}):
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="zone", multiple=False)),
            }),
            errors=errors,
        )
