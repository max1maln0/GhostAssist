"""Trusted lazy provider factories with request-scoped resource ownership."""

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType
from typing import Callable, Mapping

from core.contracts import ProviderCapabilities
from core.model_capabilities import ModelCapabilities
from core.provider_contracts import ManagedProvider, ProviderError, ProviderErrorCode


@dataclass(frozen=True)
class ProviderBinding:
    provider_id: str
    model_id: str


@dataclass(frozen=True)
class ProviderRegistration:
    identifier: str
    factory: Callable[[str], ManagedProvider] = field(repr=False)
    capabilities: ProviderCapabilities
    models: Mapping[str, ModelCapabilities]

    def __post_init__(self):
        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))


class ProviderRegistry:
    def __init__(self):
        self._lock = Lock()
        self._entries = {}
        self._closed = False
        self._active = 0

    def register(self, registration: ProviderRegistration):
        with self._lock:
            self._check_open()
            if (
                not re.fullmatch(r"[a-z][a-z0-9_-]*", registration.identifier)
                or not callable(registration.factory)
                or registration.identifier in self._entries
            ):
                raise ProviderError(
                    ProviderErrorCode.INVALID_PROVIDER,
                    "Некорректная или повторная регистрация провайдера.",
                )
            self._entries[registration.identifier] = registration

    def identifiers(self):
        with self._lock:
            return tuple(self._entries)

    def describe(self, identifier):
        with self._lock:
            self._check_open()
            entry = self._entries.get(identifier)
            if entry is None:
                raise ProviderError(
                    ProviderErrorCode.UNKNOWN_PROVIDER, "Провайдер не зарегистрирован."
                )
            return entry

    def validate(self, binding, request, *, transcription=False):
        entry = self.describe(binding.provider_id)
        model = entry.models.get(binding.model_id)
        if model is None:
            raise ProviderError(
                ProviderErrorCode.UNKNOWN_MODEL,
                "Возможности выбранной модели неизвестны. Выберите модель из проверенного каталога.",
            )
        modality = request.mime_type.split("/", 1)[0]
        allowed = {
            "text": entry.capabilities.text,
            "image": entry.capabilities.images,
            "audio": entry.capabilities.audio,
        }
        if not allowed.get(modality, False) or (
            not transcription
            and (not entry.capabilities.streaming or not entry.capabilities.text)
        ):
            raise ProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                "Адаптер не поддерживает этот сценарий.",
            )
        model.validate(request, transcription=transcription)

    def _check_open(self):
        if self._closed:
            raise ProviderError(ProviderErrorCode.CLOSED, "Реестр провайдеров закрыт.")

    @contextmanager
    def acquire(self, binding: ProviderBinding, *, transcription=False):
        entry = self.describe(binding.provider_id)
        if binding.model_id not in entry.models:
            raise ProviderError(
                ProviderErrorCode.UNKNOWN_MODEL,
                "Возможности выбранной модели неизвестны.",
            )
        with self._lock:
            self._check_open()
            self._active += 1
        provider = None
        failed = True
        try:
            try:
                provider = entry.factory(binding.model_id)
            except ProviderError:
                raise
            except Exception:  # noqa: BLE001 - sanitize trusted factory failures
                raise ProviderError(
                    ProviderErrorCode.INVALID_PROVIDER, "Не удалось создать провайдер."
                ) from None
            method = "transcribe_audio" if transcription else "generate_response"
            if not callable(getattr(provider, method, None)) or not callable(
                getattr(provider, "close", None)
            ):
                raise ProviderError(
                    ProviderErrorCode.INVALID_PROVIDER,
                    "Провайдер не реализует необходимый интерфейс или close.",
                )
            with self._lock:
                self._check_open()
            yield provider
            failed = False
        finally:
            try:
                if provider is not None and callable(getattr(provider, "close", None)):
                    try:
                        provider.close()
                    except Exception:  # noqa: BLE001 - never leak close exceptions
                        if not failed:
                            raise ProviderError(
                                ProviderErrorCode.PROVIDER_ERROR,
                                "Не удалось освободить ресурсы провайдера.",
                            ) from None
            finally:
                with self._lock:
                    self._active -= 1

    @property
    def active(self):
        with self._lock:
            return self._active

    def close(self):
        # Active leases own their clients until their worker unwinds. Never
        # close a live SDK from the UI thread; do not block Tk on native calls.
        with self._lock:
            self._closed = True
