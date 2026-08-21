"""The interface — stdlib-served, self-contained, offline-capable.

Every case the platform handles is meant to be conducted through here: ask a
question and get an answer with its reasoning and its gaps; fire a scenario and
watch the views react over the live event feed; flip one tag to point the same
configuration at the real environment.

No CDN and no build step, because the zero-dependency rule includes the UI. An
architecture intelligence platform gets deployed inside private infrastructure,
and a page that fetched a script from the internet would make every page load an
outbound request from in there.
"""

from __future__ import annotations

from .api import Api, Request, Response
from .server import APP_ROOT, UiServer
from .stream import Client, EventStream

__all__ = ["UiServer", "Api", "Request", "Response", "EventStream", "Client", "APP_ROOT"]
