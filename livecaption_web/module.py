"""Caption routes. Capture starts only when the page asks it to."""

from __future__ import annotations

import asyncio
import json

from urllib.parse import quote

from starlette.responses import RedirectResponse, StreamingResponse

from modules.caption.service import CaptionService


def register(ctx) -> None:
    from core.texts import register as register_texts
    register_texts(ctx)
    ctx.caption = CaptionService(ctx)
    ctx.routes.append(("/captions", captions_page, ["GET"]))
    ctx.routes.append(("/captions/events", captions_events, ["GET"]))
    ctx.routes.append(("/captions/start", captions_start, ["POST"]))
    ctx.routes.append(("/captions/stop", captions_stop, ["POST"]))


async def captions_page(request):
    ctx = request.app.state.ctx
    service = ctx.caption
    return ctx.templates.TemplateResponse(
        request,
        "captions.html",
        {
            "summary_enabled": "summary" in ctx.enabled,
            "error": service.error,
            "listening": bool(service.session_id),
            "queue_length": service.stats.get("length", 0),
            "queue_max": service.stats.get("max", 0),
            "dropped": service.stats.get("dropped", 0),
            "message": request.query_params.get("msg", ""),
        },
    )


async def captions_events(request):
    service = request.app.state.ctx.caption
    box = service.subscribe()

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.to_thread(box.get, True, 1.0)
                except Exception:
                    yield ": keepalive\n\n"
                    continue
                yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
        finally:
            service.unsubscribe(box)

    return StreamingResponse(generate(), media_type="text/event-stream")


async def captions_start(request):
    error = await asyncio.to_thread(request.app.state.ctx.caption.start)
    target = "/captions" if not error else "/captions?msg=" + quote(error)
    return RedirectResponse(target, status_code=303)


async def captions_stop(request):
    await asyncio.to_thread(request.app.state.ctx.caption.stop)
    return RedirectResponse("/captions", status_code=303)
