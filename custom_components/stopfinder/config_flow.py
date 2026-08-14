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
    CONF_MINUTES_BEFORE,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_USER_AGENT,
    CONF_USERNAME,
    DEFAULT_MINUTES_BEFORE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_USER_AGENT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _poll_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1, max=300, step=1,
            unit_of_measurement="seconds",
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _minutes_before_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=30, step=1,
            unit_of_measurement="minutes",
            mode=selector.NumberSelectorMode.SLIDER,
        )
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
    """Multi-step setup wizard: credentials → poll interval."""

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
        if user_input is not None:
            return self.async_create_entry(title="Stopfinder", data={
                **self._credentials,
                CONF_POLL_INTERVAL:   user_input[CONF_POLL_INTERVAL],
                CONF_MINUTES_BEFORE:  user_input[CONF_MINUTES_BEFORE],
            })

        return self.async_show_form(
            step_id="preferences",
            data_schema=vol.Schema({
                vol.Required(CONF_POLL_INTERVAL,  default=DEFAULT_POLL_INTERVAL):  _poll_selector(),
                vol.Required(CONF_MINUTES_BEFORE, default=DEFAULT_MINUTES_BEFORE): _minutes_before_selector(),
            }),
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
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        def _cur(key, default):
            return self.config_entry.options.get(
                key, self.config_entry.data.get(key, default)
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_POLL_INTERVAL,  default=_cur(CONF_POLL_INTERVAL,  DEFAULT_POLL_INTERVAL)):  _poll_selector(),
                vol.Required(CONF_MINUTES_BEFORE, default=_cur(CONF_MINUTES_BEFORE, DEFAULT_MINUTES_BEFORE)): _minutes_before_selector(),
            }),
        )
