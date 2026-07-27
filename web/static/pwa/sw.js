"use strict";

const SHELL_CACHE = "school-ai-shell-v8";
const PUBLIC_FEED_CACHE = "school-ai-public-feed-v1";
const SHELL_ASSETS = [
    "/",
    "/kiosk",
    "/screen",
    "/pair?profile=kiosk",
    "/pair?profile=screen",
    "/manifest-kiosk.webmanifest",
    "/manifest-screen.webmanifest",
    "/static/pwa/pwa.css",
    "/static/pwa/common.js",
    "/static/pwa/pair.js",
    "/static/pwa/kiosk.js",
    "/static/pwa/screen.js",
    "/static/pwa/vendor/zxing-browser-0.2.1.min.js",
    "/static/pwa/icons/icon-192.png",
    "/static/pwa/icons/icon-512.png",
    "/static/pwa/icons/icon-maskable-512.png",
    "/static/favicon.png",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(SHELL_CACHE)
            .then((cache) => cache.addAll(SHELL_ASSETS))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    const retained = new Set([SHELL_CACHE, PUBLIC_FEED_CACHE]);
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys
                    .filter((key) => key.startsWith("school-ai-") && !retained.has(key))
                    .map((key) => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

async function networkFirst(request) {
    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(SHELL_CACHE);
            await cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        const cached = await caches.match(request);
        if (cached) {
            return cached;
        }
        const fallback = await caches.match("/");
        if (fallback) {
            return fallback;
        }
        throw error;
    }
}

async function publicFeed(request) {
    const cache = await caches.open(PUBLIC_FEED_CACHE);
    try {
        const response = await fetch(request);
        if (response.ok) {
            await cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        const cached = await cache.match(request);
        if (cached) {
            return cached;
        }
        throw error;
    }
}

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") {
        return;
    }
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return;
    }

    if (url.pathname === "/api/screen/feed") {
        event.respondWith(publicFeed(request));
        return;
    }
    if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/admin")) {
        return;
    }
    if (request.mode === "navigate") {
        event.respondWith(networkFirst(request));
        return;
    }
    if (
        url.pathname.startsWith("/static/pwa/")
        || url.pathname.startsWith("/manifest-")
        || url.pathname === "/favicon.ico"
    ) {
        event.respondWith(
            caches.match(request).then((cached) => cached || fetch(request))
        );
    }
});
