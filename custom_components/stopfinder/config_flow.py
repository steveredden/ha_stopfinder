"""Config flow and options flow for Stopfinder."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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


def _window_selector() -> selector.NumberSelector:
    """Return a 0–60 min slider selector (built lazily, not at module load)."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=60, step=5,
            unit_of_measurement="min",
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _poll_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=5, max=300, step=5,
            unit_of_measurement="seconds",
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _hour_selector(min_h: int, max_h: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_h, max=max_h, step=1,
            unit_of_measurement="h (24h)",
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _preferences_schema(
    defaults: dict[str, Any],
    zone_defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the tracking-preferences schema shared by setup and options.

    ``defaults`` supplies the current/default value for each preference key.
    When ``zone_defaults`` is given (options flow only) the two optional zone
    selectors are appended.
    """
    schema: dict[Any, Any] = {
        vol.Required(CONF_POLL_INTERVAL, default=defaults[CONF_POLL_INTERVAL]):
            _poll_selector(),
        vol.Required(CONF_MINUTES_BEFORE_HOME_PICKUP,
                     default=defaults[CONF_MINUTES_BEFORE_HOME_PICKUP]):
            _window_selector(),
        vol.Required(CONF_MINUTES_AFTER_SCHOOL_DROPOFF,
                     default=defaults[CONF_MINUTES_AFTER_SCHOOL_DROPOFF]):
            _window_selector(),
        vol.Required(CONF_MINUTES_BEFORE_SCHOOL_PICKUP,
                     default=defaults[CONF_MINUTES_BEFORE_SCHOOL_PICKUP]):
            _window_selector(),
        vol.Required(CONF_MINUTES_AFTER_HOME_DROPOFF,
                     default=defaults[CONF_MINUTES_AFTER_HOME_DROPOFF]):
            _window_selector(),
        vol.Required(CONF_HALFDAY_HOUR, default=defaults[CONF_HALFDAY_HOUR]):
            _hour_selector(8, 15),
        vol.Required(CONF_EARLY_HOUR, default=defaults[CONF_EARLY_HOUR]):
            _hour_selector(9, 16),
    }

    if zone_defaults is not None:
        schema[vol.Optional(
            CONF_ZONE_NEIGHBORHOOD,
            description={"suggested_value": zone_defaults.get(CONF_ZONE_NEIGHBORHOOD)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="zone", multiple=False)
        )
        schema[vol.Optional(
            CONF_ZONE_SCHOOL,
            description={"suggested_value": zone_defaults.get(CONF_ZONE_SCHOOL)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="zone", multiple=False)
        )

    return vol.Schema(schema)


def _validate_thresholds(user_input: dict[str, Any]) -> dict[str, str]:
    """Return form errors if the early-release hour isn't after the half-day hour."""
    if int(user_input[CONF_EARLY_HOUR]) <= int(user_input[CONF_HALFDAY_HOUR]):
        return {"base": "invalid_thresholds"}
    return {}


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
    MINOR_VERSION = 1

    _credentials: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
    ) -> ConfigFlowResult:
        errors = _validate_thresholds(user_input) if user_input is not None else {}

        if user_input is not None and not errors:
            return self.async_create_entry(title="Stopfinder", data={
                **self._credentials,
                CONF_POLL_INTERVAL:                user_input[CONF_POLL_INTERVAL],
                CONF_MINUTES_BEFORE_HOME_PICKUP:   user_input[CONF_MINUTES_BEFORE_HOME_PICKUP],
                CONF_MINUTES_AFTER_SCHOOL_DROPOFF: user_input[CONF_MINUTES_AFTER_SCHOOL_DROPOFF],
                CONF_MINUTES_BEFORE_SCHOOL_PICKUP: user_input[CONF_MINUTES_BEFORE_SCHOOL_PICKUP],
                CONF_MINUTES_AFTER_HOME_DROPOFF:   user_input[CONF_MINUTES_AFTER_HOME_DROPOFF],
                CONF_HALFDAY_HOUR:                 int(user_input[CONF_HALFDAY_HOUR]),
                CONF_EARLY_HOUR:                   int(user_input[CONF_EARLY_HOUR]),
            })

        defaults = {
            CONF_POLL_INTERVAL:                DEFAULT_POLL_INTERVAL,
            CONF_MINUTES_BEFORE_HOME_PICKUP:   DEFAULT_MINUTES_BEFORE_HOME_PICKUP,
            CONF_MINUTES_AFTER_SCHOOL_DROPOFF: DEFAULT_MINUTES_AFTER_SCHOOL_DROPOFF,
            CONF_MINUTES_BEFORE_SCHOOL_PICKUP: DEFAULT_MINUTES_BEFORE_SCHOOL_PICKUP,
            CONF_MINUTES_AFTER_HOME_DROPOFF:   DEFAULT_MINUTES_AFTER_HOME_DROPOFF,
            CONF_HALFDAY_HOUR:                 HALFDAY_THRESHOLD_HOUR,
            CONF_EARLY_HOUR:                   EARLY_THRESHOLD_HOUR,
        }
        return self.async_show_form(
            step_id="preferences",
            data_schema=_preferences_schema(defaults),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_options_flow(cls, config_entry: ConfigEntry) -> OptionsFlow:
        return StopfinderOptionsFlow()


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

class StopfinderOptionsFlow(OptionsFlow):
    """Adjust settings post-setup.

    self.config_entry is injected automatically by HA (2024.4+).
    Do not pass config_entry to __init__.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = _validate_thresholds(user_input) if user_input is not None else {}

        if user_input is not None and not errors:
            if not user_input.get(CONF_ZONE_NEIGHBORHOOD):
                user_input.pop(CONF_ZONE_NEIGHBORHOOD, None)
            if not user_input.get(CONF_ZONE_SCHOOL):
                user_input.pop(CONF_ZONE_SCHOOL, None)
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        data = self.config_entry.data

        def _get(key: str, legacy_key: str, default: Any) -> Any:
            return opts.get(key, data.get(key, opts.get(legacy_key, data.get(legacy_key, default))))

        defaults = {
            CONF_POLL_INTERVAL:                opts.get(CONF_POLL_INTERVAL, data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
            CONF_MINUTES_BEFORE_HOME_PICKUP:   _get(CONF_MINUTES_BEFORE_HOME_PICKUP,   "minutes_before", DEFAULT_MINUTES_BEFORE_HOME_PICKUP),
            CONF_MINUTES_AFTER_SCHOOL_DROPOFF: _get(CONF_MINUTES_AFTER_SCHOOL_DROPOFF, "minutes_after",  DEFAULT_MINUTES_AFTER_SCHOOL_DROPOFF),
            CONF_MINUTES_BEFORE_SCHOOL_PICKUP: _get(CONF_MINUTES_BEFORE_SCHOOL_PICKUP, "minutes_before", DEFAULT_MINUTES_BEFORE_SCHOOL_PICKUP),
            CONF_MINUTES_AFTER_HOME_DROPOFF:   _get(CONF_MINUTES_AFTER_HOME_DROPOFF,   "minutes_after",  DEFAULT_MINUTES_AFTER_HOME_DROPOFF),
            CONF_HALFDAY_HOUR:                 opts.get(CONF_HALFDAY_HOUR, data.get(CONF_HALFDAY_HOUR, HALFDAY_THRESHOLD_HOUR)),
            CONF_EARLY_HOUR:                   opts.get(CONF_EARLY_HOUR, data.get(CONF_EARLY_HOUR, EARLY_THRESHOLD_HOUR)),
        }
        zone_defaults = {
            CONF_ZONE_NEIGHBORHOOD: opts.get(CONF_ZONE_NEIGHBORHOOD),
            CONF_ZONE_SCHOOL:       opts.get(CONF_ZONE_SCHOOL),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=_preferences_schema(defaults, zone_defaults),
            errors=errors,
        )
