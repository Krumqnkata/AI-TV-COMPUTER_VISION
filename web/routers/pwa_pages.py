"""HTML shell, manifests and service worker for the browser kiosk profiles."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from utils.config import Config


router = APIRouter(tags=["PWA pages"])
templates = Jinja2Templates(directory=str(Config.PROJECT_ROOT / "web" / "templates"))
PWA_STATIC = Config.PROJECT_ROOT / "web" / "static" / "pwa"


def _page_context(request: Request, profile: str) -> dict:
    return {
        "request": request,
        "profile": profile,
        "manifest_url": f"/manifest-{profile}.webmanifest",
    }


@router.get("/pair", include_in_schema=False)
async def pair_page(
    request: Request,
    profile: str = Query(default="kiosk", pattern="^(kiosk|screen)$"),
):
    return templates.TemplateResponse(
        request,
        "pwa/pair.html",
        _page_context(request, profile),
    )


@router.get("/kiosk", include_in_schema=False)
async def kiosk_page(request: Request):
    return templates.TemplateResponse(
        request,
        "pwa/kiosk.html",
        _page_context(request, "kiosk"),
    )


@router.get("/screen", include_in_schema=False)
async def screen_page(request: Request):
    return templates.TemplateResponse(
        request,
        "pwa/screen.html",
        _page_context(request, "screen"),
    )


def _manifest(profile: str) -> dict:
    kiosk = profile == "kiosk"
    title = "School AI Киоск" if kiosk else "School AI Екран"
    start_url = "/kiosk" if kiosk else "/screen"
    return {
        "id": f"/pwa/{profile}",
        "name": title,
        "short_name": "AI Киоск" if kiosk else "AI Екран",
        "description": (
            "Интерактивен училищен киоск за QR баджове, справки и съобщения."
            if kiosk
            else "Училищен информационен и сдвоен публичен екран."
        ),
        "lang": "bg",
        "dir": "ltr",
        "start_url": start_url,
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui", "browser"],
        "orientation": "any",
        "background_color": "#f3f7fb",
        "theme_color": "#123b6d",
        "icons": [
            {
                "src": "/static/pwa/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/pwa/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/pwa/icons/icon-maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "shortcuts": [
            {
                "name": "Отвори киоска" if kiosk else "Отвори екрана",
                "short_name": "Киоск" if kiosk else "Екран",
                "url": start_url,
                "icons": [{
                    "src": "/static/pwa/icons/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                }],
            }
        ],
    }


@router.get("/manifest-kiosk.webmanifest", include_in_schema=False)
async def kiosk_manifest():
    return JSONResponse(
        _manifest("kiosk"),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/manifest-screen.webmanifest", include_in_schema=False)
async def screen_manifest():
    return JSONResponse(
        _manifest("screen"),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/kiosk-sw.js", include_in_schema=False)
async def pwa_service_worker():
    return FileResponse(
        PWA_STATIC / "sw.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )
