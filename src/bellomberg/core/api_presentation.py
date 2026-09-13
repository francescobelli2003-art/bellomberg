"""Expose authored language variants to the desktop without recalculating data.

Only explicit ``message`` instances carry variants. Source strings, historical
documents, keys, numerical values and operational codes are never translated.
The extra metadata exists at the HTTP boundary; it is not stored in the DB or
sent to model tools. The client can switch cached views without a provider call.
"""
from starlette.responses import JSONResponse

from bellomberg.core.presentation import _PresentationText, render_payload


class PresentationJSONResponse(JSONResponse):
    def render(self, content):
        if not isinstance(content, dict):
            return super().render(content)
        if "_presentation_v1" in content:
            raise ValueError("Reserved presentation metadata already exists")
        texts = []
        rendered = render_payload(content)

        def collect(value, path):
            if isinstance(value, _PresentationText):
                texts.append({"path": path,
                              "it": str(render_payload(value, language="it")),
                              "en": str(render_payload(value, language="en"))})
            elif isinstance(value, dict):
                for key, child in value.items():
                    collect(child, [*path, key])
            elif isinstance(value, (list, tuple)):
                for index, child in enumerate(value):
                    collect(child, [*path, index])

        collect(rendered, [])
        if texts:
            rendered["_presentation_v1"] = {"version": 1, "texts": texts}
        return super().render(rendered)
