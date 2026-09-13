"""Capture language for the whole ASGI call, including streamed response bodies."""
from starlette.responses import JSONResponse

from bellomberg.core.language import language_context, validate_language
from bellomberg.storage.preferences import PreferenceError, read_preferences


class LanguageMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        values = [value for key, value in scope.get("headers", []) if key.lower() == b"x-bb-language"]
        warning = None
        if values:
            try:
                if len(values) != 1:
                    raise ValueError("duplicate language header")
                selected = validate_language(values[0].decode("ascii"))
                source = "header"
            except (ValueError, UnicodeError):
                response = JSONResponse(status_code=400, content={
                    "code": "unsupported_language",
                    "detail": "Lingua non supportata / Unsupported language: X-BB-Language must be 'it' or 'en'."})
                return await response(scope, receive, send)
        else:
            try:
                preference = read_preferences()
                selected, source = preference["language"], preference["source"]
            except PreferenceError:
                path = scope.get("path", "")
                if path not in {"/health", "/auth/login", "/auth/status", "/preferences"}:
                    response = JSONResponse(status_code=503, content={
                        "code": "language_preference_unavailable",
                        "detail": "Preferenza lingua illeggibile / Saved language preference unreadable. "
                                  "Apri Preferenze / Open Preferences, or send an explicit X-BB-Language."})
                    return await response(scope, receive, send)
                selected, source, warning = "it", "bootstrap_default", "preferences_unavailable"
        state = scope.setdefault("state", {})
        state["output_language"], state["language_source"] = selected, source
        async def localized_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in {b"content-language", b"x-bb-language-source", b"x-bb-language-warning"}]
                headers.extend([(b"content-language", selected.encode()), (b"x-bb-language-source", source.encode())])
                if warning:
                    headers.append((b"x-bb-language-warning", warning.encode()))
                message["headers"] = headers
            await send(message)
        with language_context(selected):
            await self.app(scope, receive, localized_send)
