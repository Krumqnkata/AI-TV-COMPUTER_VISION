(function () {
    "use strict";

    const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
    const DB_NAME = "school-ai-pwa";
    const DB_VERSION = 1;
    const ACK_STORE = "delivery_ack";
    const PROFILE_CAPABILITIES = {
        kiosk: ["audio", "camera", "kiosk", "qr", "screen", "tts"],
        screen: ["screen"],
    };
    const knownConfigVersions = new Map();
    const diagnosticOverrides = new Map();
    const diagnosticTimers = new Map();

    class ApiError extends Error {
        constructor(message, status, payload) {
            super(message);
            this.name = "ApiError";
            this.status = status;
            this.payload = payload;
        }
    }

    function getCookie(name) {
        const prefix = `${encodeURIComponent(name)}=`;
        const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
        return item ? decodeURIComponent(item.slice(prefix.length)) : "";
    }

    async function api(path, options) {
        const settings = options || {};
        const method = (settings.method || "GET").toUpperCase();
        const headers = new Headers(settings.headers || {});
        headers.set("Accept", "application/json");

        let body = settings.body;
        if (body !== undefined && body !== null && !(body instanceof FormData) && typeof body !== "string") {
            headers.set("Content-Type", "application/json");
            body = JSON.stringify(body);
        }
        if (UNSAFE_METHODS.has(method)) {
            const csrfToken = getCookie("csrf_token");
            if (csrfToken) {
                headers.set("X-CSRF-Token", csrfToken);
            }
        }

        const response = await fetch(path, {
            method,
            headers,
            body,
            credentials: "same-origin",
            cache: settings.cache || "no-store",
            signal: settings.signal,
            keepalive: Boolean(settings.keepalive),
        });

        let payload = null;
        if (response.status !== 204 && response.status !== 304) {
            const contentType = response.headers.get("content-type") || "";
            payload = contentType.includes("application/json")
                ? await response.json()
                : await response.text();
        }
        if (!response.ok) {
            const detail = payload && typeof payload === "object" ? payload.detail : payload;
            throw new ApiError(detail || `HTTP ${response.status}`, response.status, payload);
        }
        return payload;
    }

    function newUuid() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }
        const random = new Uint8Array(16);
        window.crypto.getRandomValues(random);
        random[6] = (random[6] & 0x0f) | 0x40;
        random[8] = (random[8] & 0x3f) | 0x80;
        const hex = Array.from(random, (value) => value.toString(16).padStart(2, "0")).join("");
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    function installationId(profile) {
        const key = `school-ai-installation:${profile}`;
        let id = localStorage.getItem(key);
        if (!id) {
            id = `pwa-${profile}-${newUuid()}`;
            localStorage.setItem(key, id);
        }
        return id;
    }

    function suggestedDeviceName(profile) {
        const kind = profile === "screen" ? "Екран" : "Киоск";
        const platform = navigator.userAgentData && navigator.userAgentData.platform
            ? navigator.userAgentData.platform
            : navigator.platform || "браузър";
        return `${kind} — ${platform}`.slice(0, 150);
    }

    function browserName() {
        const brands = navigator.userAgentData && navigator.userAgentData.brands;
        if (Array.isArray(brands)) {
            const preferred = brands.find((item) => !/not.?a.?brand/i.test(item.brand));
            if (preferred && preferred.brand) {
                return `${preferred.brand} ${preferred.version || ""}`.trim().slice(0, 80);
            }
        }
        const agent = navigator.userAgent || "";
        const candidates = [
            ["Edge", /Edg\/([\d.]+)/],
            ["Firefox", /Firefox\/([\d.]+)/],
            ["Chrome", /Chrome\/([\d.]+)/],
            ["Safari", /Version\/([\d.]+).*Safari/],
        ];
        for (const [name, pattern] of candidates) {
            const match = agent.match(pattern);
            if (match) {
                return `${name} ${match[1]}`.slice(0, 80);
            }
        }
        return "Неизвестен браузър";
    }

    async function cameraPermission() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            return "unavailable";
        }
        if (!navigator.permissions || typeof navigator.permissions.query !== "function") {
            return "unknown";
        }
        try {
            const permission = await navigator.permissions.query({ name: "camera" });
            return permission.state || "unknown";
        } catch (_error) {
            return "unknown";
        }
    }

    async function collectDiagnostics(profile) {
        const overrides = diagnosticOverrides.get(profile) || {};
        const platform = navigator.userAgentData && navigator.userAgentData.platform
            ? navigator.userAgentData.platform
            : navigator.platform || "неизвестна";
        return {
            browser: browserName(),
            platform: String(platform).slice(0, 80),
            language: String(navigator.language || "").slice(0, 30) || undefined,
            secure_context: Boolean(window.isSecureContext),
            standalone: Boolean(
                window.matchMedia("(display-mode: standalone)").matches
                || window.navigator.standalone
            ),
            camera_api: Boolean(
                navigator.mediaDevices
                && typeof navigator.mediaDevices.getUserMedia === "function"
            ),
            camera_permission: await cameraPermission(),
            camera_status: profile === "kiosk" ? "unknown" : "not_applicable",
            barcode_detector: "BarcodeDetector" in window,
            service_worker: "serviceWorker" in navigator,
            indexed_db: "indexedDB" in window,
            web_socket: "WebSocket" in window,
            speech_synthesis: "speechSynthesis" in window,
            viewport_width: Math.max(0, Math.round(window.innerWidth || 0)),
            viewport_height: Math.max(0, Math.round(window.innerHeight || 0)),
            ...overrides,
        };
    }

    function reportDiagnostics(profile, values, sendImmediately = true) {
        diagnosticOverrides.set(profile, {
            ...(diagnosticOverrides.get(profile) || {}),
            ...(values || {}),
        });
        if (!sendImmediately || !knownConfigVersions.has(profile)) {
            return;
        }
        window.clearTimeout(diagnosticTimers.get(profile));
        diagnosticTimers.set(profile, window.setTimeout(() => {
            diagnosticTimers.delete(profile);
            sendHeartbeat(profile);
        }, 250));
    }

    function setConnectionState(state, label) {
        document.querySelectorAll("[data-connection-status]").forEach((element) => {
            element.dataset.state = state;
            const dot = element.querySelector("span");
            element.replaceChildren();
            if (dot) {
                element.append(dot);
            } else {
                const newDot = document.createElement("span");
                newDot.setAttribute("aria-hidden", "true");
                element.append(newDot);
            }
            element.append(document.createTextNode(` ${label}`));
        });
    }

    function updateOnlineState() {
        const offline = !navigator.onLine;
        document.querySelectorAll("[data-offline-banner]").forEach((element) => {
            element.hidden = !offline;
        });
        if (offline) {
            setConnectionState("offline", "Офлайн");
        }
    }

    function startClock() {
        const render = () => {
            const now = new Date();
            const time = new Intl.DateTimeFormat("bg-BG", {
                hour: "2-digit",
                minute: "2-digit",
            }).format(now);
            const date = new Intl.DateTimeFormat("bg-BG", {
                weekday: "long",
                day: "numeric",
                month: "long",
            }).format(now);
            document.querySelectorAll("[data-clock]").forEach((element) => {
                element.textContent = time;
                element.setAttribute("datetime", now.toISOString());
            });
            document.querySelectorAll("[data-date]").forEach((element) => {
                element.textContent = date.charAt(0).toUpperCase() + date.slice(1);
            });
        };
        render();
        return window.setInterval(render, 15_000);
    }

    let installPrompt = null;

    function setupInstallPrompt() {
        const buttons = Array.from(document.querySelectorAll("[data-install-button]"));
        window.addEventListener("beforeinstallprompt", (event) => {
            event.preventDefault();
            installPrompt = event;
            buttons.forEach((button) => {
                button.hidden = false;
            });
        });
        buttons.forEach((button) => {
            button.addEventListener("click", async () => {
                if (!installPrompt) {
                    return;
                }
                installPrompt.prompt();
                await installPrompt.userChoice;
                installPrompt = null;
                buttons.forEach((item) => {
                    item.hidden = true;
                });
            });
        });
        window.addEventListener("appinstalled", () => {
            installPrompt = null;
            buttons.forEach((button) => {
                button.hidden = true;
            });
        });
    }

    async function registerServiceWorker() {
        if (!("serviceWorker" in navigator)) {
            return null;
        }
        try {
            return await navigator.serviceWorker.register("/kiosk-sw.js", { scope: "/" });
        } catch (error) {
            console.warn("Service worker registration failed", error);
            return null;
        }
    }

    function openDatabase() {
        if (!("indexedDB" in window)) {
            return Promise.resolve(null);
        }
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);
            request.onupgradeneeded = () => {
                const database = request.result;
                if (!database.objectStoreNames.contains(ACK_STORE)) {
                    const store = database.createObjectStore(ACK_STORE, { keyPath: "delivery_id" });
                    store.createIndex("profile", "profile", { unique: false });
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    async function withAckStore(mode, callback) {
        const database = await openDatabase();
        if (!database) {
            return null;
        }
        return new Promise((resolve, reject) => {
            const transaction = database.transaction(ACK_STORE, mode);
            const store = transaction.objectStore(ACK_STORE);
            let result;
            try {
                result = callback(store);
            } catch (error) {
                reject(error);
                return;
            }
            transaction.oncomplete = () => {
                database.close();
                resolve(result);
            };
            transaction.onerror = () => {
                database.close();
                reject(transaction.error);
            };
        });
    }

    async function queueDeliveryAck(profile, deliveryId, messageIds) {
        if (!deliveryId) {
            return;
        }
        let persisted = false;
        try {
            persisted = await withAckStore("readwrite", (store) => {
                store.put({
                    delivery_id: deliveryId,
                    message_ids: Array.from(messageIds || [], Number),
                    profile,
                    queued_at: Date.now(),
                });
                return true;
            });
        } catch (error) {
            console.warn("Could not persist delivery acknowledgment", error);
        }
        if (!persisted) {
            try {
                await api(`/api/${profile}/deliveries/ack`, {
                    method: "POST",
                    body: {
                        delivery_id: deliveryId,
                        message_ids: Array.from(messageIds || [], Number),
                    },
                });
            } catch (error) {
                console.warn("Could not send non-persistent delivery acknowledgment", error);
            }
            return;
        }
        await flushDeliveryAcks(profile);
    }

    async function listDeliveryAcks(profile) {
        const database = await openDatabase();
        if (!database) {
            return [];
        }
        return new Promise((resolve, reject) => {
            const transaction = database.transaction(ACK_STORE, "readonly");
            const index = transaction.objectStore(ACK_STORE).index("profile");
            const request = index.getAll(profile);
            request.onsuccess = () => resolve(request.result || []);
            request.onerror = () => reject(request.error);
            transaction.oncomplete = () => database.close();
        });
    }

    async function deleteDeliveryAck(deliveryId) {
        await withAckStore("readwrite", (store) => {
            store.delete(deliveryId);
        });
    }

    let ackFlushActive = false;

    async function flushDeliveryAcks(profile) {
        if (ackFlushActive || !navigator.onLine) {
            return;
        }
        ackFlushActive = true;
        try {
            const pending = await listDeliveryAcks(profile);
            for (const item of pending) {
                try {
                    await api(`/api/${profile}/deliveries/ack`, {
                        method: "POST",
                        body: {
                            delivery_id: item.delivery_id,
                            message_ids: item.message_ids,
                        },
                    });
                    await deleteDeliveryAck(item.delivery_id);
                } catch (error) {
                    if (error instanceof ApiError && [400, 403, 404, 409].includes(error.status)) {
                        await deleteDeliveryAck(item.delivery_id);
                        continue;
                    }
                    break;
                }
            }
        } catch (error) {
            console.warn("Could not flush delivery acknowledgments", error);
        } finally {
            ackFlushActive = false;
        }
    }

    const seenEvents = new Map();

    function acceptEvent(eventId) {
        if (!eventId) {
            return true;
        }
        const now = Date.now();
        for (const [key, timestamp] of seenEvents) {
            if (now - timestamp > 10 * 60_000) {
                seenEvents.delete(key);
            }
        }
        if (seenEvents.has(eventId)) {
            return false;
        }
        seenEvents.set(eventId, now);
        return true;
    }

    function profileSocket(profile, onMessage) {
        const OFFLINE_RETRY_MS = 1_000;
        const LIVENESS_TIMEOUT_MS = 5_000;
        let socket = null;
        let stopped = false;
        let attempt = 0;
        let reconnectTimer = null;
        let pingTimer = null;
        let livenessTimer = null;

        const clearConnectionTimers = () => {
            window.clearInterval(pingTimer);
            window.clearTimeout(livenessTimer);
            pingTimer = null;
            livenessTimer = null;
        };

        const retireSocket = (reason) => {
            clearConnectionTimers();
            const staleSocket = socket;
            socket = null;
            if (
                staleSocket
                && staleSocket.readyState !== WebSocket.CLOSED
                && staleSocket.readyState !== WebSocket.CLOSING
            ) {
                try {
                    staleSocket.close(1000, reason);
                } catch (_error) {
                    // A browser may reject close() while a failed connection is settling.
                }
            }
        };

        const scheduleReconnect = () => {
            if (stopped || reconnectTimer !== null) {
                return;
            }
            let delay = OFFLINE_RETRY_MS;
            if (navigator.onLine) {
                const base = Math.min(30_000, 1_000 * (2 ** Math.min(attempt, 5)));
                const jitter = Math.floor(Math.random() * 700);
                delay = base + jitter;
                attempt += 1;
            }
            reconnectTimer = window.setTimeout(() => {
                reconnectTimer = null;
                connect();
            }, delay);
        };

        const armLivenessTimeout = (currentSocket) => {
            window.clearTimeout(livenessTimer);
            livenessTimer = window.setTimeout(() => {
                if (socket === currentSocket) {
                    currentSocket.close();
                }
            }, LIVENESS_TIMEOUT_MS);
        };

        const sendPing = (currentSocket) => {
            if (socket !== currentSocket || currentSocket.readyState !== WebSocket.OPEN) {
                return;
            }
            try {
                currentSocket.send(JSON.stringify({ type: "ping" }));
                armLivenessTimeout(currentSocket);
            } catch (_error) {
                currentSocket.close();
            }
        };

        const connect = () => {
            if (stopped) {
                return;
            }
            if (!navigator.onLine) {
                setConnectionState("offline", "Офлайн");
                scheduleReconnect();
                return;
            }
            if (
                socket
                && (
                    socket.readyState === WebSocket.CONNECTING
                    || socket.readyState === WebSocket.OPEN
                )
            ) {
                return;
            }
            socket = null;
            setConnectionState("connecting", "Свързване");
            const protocol = location.protocol === "https:" ? "wss:" : "ws:";
            const currentSocket = new WebSocket(`${protocol}//${location.host}/ws/${profile}`);
            socket = currentSocket;

            currentSocket.addEventListener("open", () => {
                if (socket !== currentSocket) {
                    return;
                }
                setConnectionState("connecting", "Удостоверяване");
                window.clearInterval(pingTimer);
                pingTimer = window.setInterval(() => {
                    sendPing(currentSocket);
                }, 25_000);
                armLivenessTimeout(currentSocket);
            });
            currentSocket.addEventListener("message", (event) => {
                if (socket !== currentSocket) {
                    return;
                }
                try {
                    const message = JSON.parse(event.data);
                    if (message.type === "pong" || message.type === "registered") {
                        attempt = 0;
                        window.clearTimeout(livenessTimer);
                        livenessTimer = null;
                        setConnectionState("online", "Свързан");
                    }
                    onMessage(message);
                } catch (error) {
                    console.warn("Invalid WebSocket message", error);
                }
            });
            currentSocket.addEventListener("close", (event) => {
                if (socket !== currentSocket) {
                    return;
                }
                clearConnectionTimers();
                socket = null;
                if (stopped) {
                    return;
                }
                if (event.code === 1008) {
                    location.replace(`/pair?profile=${encodeURIComponent(profile)}`);
                    return;
                }
                setConnectionState(
                    "offline",
                    navigator.onLine ? "Прекъсната връзка" : "Офлайн"
                );
                scheduleReconnect();
            });
            currentSocket.addEventListener("error", () => {
                if (
                    socket === currentSocket
                    && currentSocket.readyState !== WebSocket.CLOSED
                ) {
                    currentSocket.close();
                }
            });
        };

        connect();
        return {
            reconnect() {
                window.clearTimeout(reconnectTimer);
                reconnectTimer = null;
                attempt = 0;
                retireSocket("Network changed");
                if (!navigator.onLine) {
                    setConnectionState("offline", "Офлайн");
                    scheduleReconnect();
                    return;
                }
                connect();
            },
            stop() {
                stopped = true;
                window.clearTimeout(reconnectTimer);
                reconnectTimer = null;
                retireSocket("Page closed");
            },
        };
    }

    function setPaused(profile, paused) {
        localStorage.setItem(`school-ai-paused:${profile}`, paused ? "1" : "0");
        const overlay = document.querySelector("[data-paused-overlay]");
        if (overlay) {
            overlay.hidden = !paused;
        }
        document.dispatchEvent(new CustomEvent("schoolai:paused", { detail: { paused } }));
    }

    function applyStoredPause(profile) {
        setPaused(profile, localStorage.getItem(`school-ai-paused:${profile}`) === "1");
    }

    async function executeCommand(profile, command) {
        let success = true;
        const result = {};
        try {
            switch (command.command) {
                case "disable":
                    setPaused(profile, true);
                    break;
                case "enable":
                    setPaused(profile, false);
                    break;
                case "restart_app":
                    result.action = "reload";
                    break;
                case "refresh_config":
                    result.action = "reload";
                    document.dispatchEvent(new CustomEvent("schoolai:command", { detail: command }));
                    break;
                case "test_camera":
                case "test_audio":
                case "test_screen":
                    document.dispatchEvent(new CustomEvent("schoolai:command", { detail: command }));
                    break;
                default:
                    success = false;
                    result.error = "unsupported_command";
            }
        } catch (error) {
            success = false;
            result.error = error instanceof Error ? error.message : String(error);
        }

        await api(`/api/${profile}/device/commands/${command.id}/ack`, {
            method: "POST",
            body: { success, result },
        });
        if (success && ["refresh_config", "restart_app"].includes(command.command)) {
            location.reload();
        }
    }

    async function pollCommands(profile) {
        try {
            const commands = await api(`/api/${profile}/device/commands/pending`);
            for (const command of commands || []) {
                await executeCommand(profile, command);
            }
        } catch (error) {
            if (error instanceof ApiError && error.status === 401) {
                location.replace(`/pair?profile=${encodeURIComponent(profile)}`);
            }
        }
    }

    async function sendHeartbeat(profile) {
        try {
            const diagnostics = await collectDiagnostics(profile);
            const heartbeat = await api(`/api/${profile}/device/heartbeat`, {
                method: "POST",
                body: {
                    status: localStorage.getItem(`school-ai-paused:${profile}`) === "1"
                        ? "paused"
                        : "online",
                    software_version: "pwa-1.1.1",
                    capabilities: PROFILE_CAPABILITIES[profile],
                    diagnostics,
                },
            });
            const knownVersion = knownConfigVersions.get(profile);
            if (
                knownVersion !== undefined
                && Number(heartbeat.config_version) !== Number(knownVersion)
            ) {
                location.reload();
                return;
            }
            knownConfigVersions.set(profile, heartbeat.config_version);
        } catch (error) {
            if (error instanceof ApiError && error.status === 401) {
                location.replace(`/pair?profile=${encodeURIComponent(profile)}`);
            }
        }
    }

    function applyDeviceConfig(config) {
        document.querySelectorAll("[data-device-label]").forEach((element) => {
            element.textContent = config.device_name || (
                config.profile === "screen" ? "Информационен екран" : "Училищен киоск"
            );
        });
        document.querySelectorAll("[data-zone-label]").forEach((element) => {
            const zone = config.zone_id || "без зона";
            const screen = config.screen_id ? ` · ${config.screen_id}` : "";
            element.textContent = `${config.device_name || config.device_id} · ${zone}${screen}`;
        });
        let dimmer = document.querySelector("[data-display-dimmer]");
        if (!dimmer) {
            dimmer = document.createElement("div");
            dimmer.className = "display-dimmer";
            dimmer.dataset.displayDimmer = "";
            dimmer.setAttribute("aria-hidden", "true");
            document.body.append(dimmer);
        }
        const brightness = Number(config.settings && config.settings.display_brightness);
        const safeBrightness = Number.isFinite(brightness)
            ? Math.max(10, Math.min(100, brightness))
            : 100;
        dimmer.dataset.brightness = String(Math.round(safeBrightness / 10) * 10);
    }

    async function unpair(profile) {
        const confirmed = window.confirm(
            "Да се премахне ли сдвояването от това устройство? Ще е необходим нов еднократен код."
        );
        if (!confirmed) {
            return;
        }
        try {
            await api(`/api/${profile}/unpair`, { method: "POST", body: {} });
        } finally {
            localStorage.removeItem(`school-ai-paused:${profile}`);
            location.replace(`/pair?profile=${encodeURIComponent(profile)}`);
        }
    }

    async function bootProfile(profile, onSocketMessage) {
        startClock();
        applyStoredPause(profile);
        setConnectionState("connecting", "Свързване");

        let config;
        try {
            config = await api(`/api/${profile}/bootstrap`);
        } catch (error) {
            if (error instanceof ApiError && error.status === 401) {
                location.replace(`/pair?profile=${encodeURIComponent(profile)}`);
                return null;
            }
            throw error;
        }
        applyDeviceConfig(config);
        knownConfigVersions.set(profile, config.config_version);
        const socket = profileSocket(profile, onSocketMessage);

        await Promise.allSettled([
            sendHeartbeat(profile),
            pollCommands(profile),
            flushDeliveryAcks(profile),
        ]);
        const heartbeatTimer = window.setInterval(() => sendHeartbeat(profile), 30_000);
        const commandTimer = window.setInterval(() => pollCommands(profile), 10_000);

        const onlineHandler = () => {
            updateOnlineState();
            socket.reconnect();
            sendHeartbeat(profile);
            flushDeliveryAcks(profile);
        };
        const offlineHandler = () => {
            updateOnlineState();
            socket.reconnect();
        };
        window.addEventListener("online", onlineHandler);
        window.addEventListener("offline", offlineHandler);

        document.querySelectorAll("[data-unpair-device]").forEach((button) => {
            button.addEventListener("click", () => unpair(profile));
        });

        return {
            config,
            socket,
            destroy() {
                window.clearInterval(heartbeatTimer);
                window.clearInterval(commandTimer);
                window.clearTimeout(diagnosticTimers.get(profile));
                diagnosticTimers.delete(profile);
                window.removeEventListener("online", onlineHandler);
                window.removeEventListener("offline", offlineHandler);
                socket.stop();
            },
        };
    }

    setupInstallPrompt();
    updateOnlineState();
    window.addEventListener("online", updateOnlineState);
    window.addEventListener("offline", updateOnlineState);
    registerServiceWorker();

    window.SchoolAI = {
        ApiError,
        acceptEvent,
        api,
        applyDeviceConfig,
        bootProfile,
        flushDeliveryAcks,
        installationId,
        newUuid,
        queueDeliveryAck,
        reportDiagnostics,
        registerServiceWorker,
        setConnectionState,
        startClock,
        suggestedDeviceName,
    };
}());
