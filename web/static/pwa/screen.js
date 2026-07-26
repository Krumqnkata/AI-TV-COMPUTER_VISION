(function () {
    "use strict";

    const app = window.SchoolAI;
    const feedContainer = document.querySelector("[data-public-feed]");
    const emptyState = document.querySelector("[data-screen-empty]");
    const slide = document.querySelector("[data-screen-slide]");
    const slideType = document.querySelector("[data-slide-type]");
    const slideTitle = document.querySelector("[data-slide-title]");
    const slideBody = document.querySelector("[data-slide-body]");
    const slideMeta = document.querySelector("[data-slide-meta]");
    const slideCounter = document.querySelector("[data-slide-counter]");
    const progress = document.querySelector("[data-slide-progress]");
    const personalOverlay = document.querySelector("[data-personal-overlay]");
    const personalCloseButton = document.querySelector("[data-screen-close-session]");

    let profileController = null;
    let config = null;
    let slides = [];
    let slideIndex = 0;
    let rotationTimer = null;
    let feedRefreshTimer = null;
    let personalTimer = null;
    let screenMode = "public";

    function setText(selector, value) {
        const element = document.querySelector(selector);
        if (element) {
            element.textContent = value || "";
        }
    }

    function formatDateTime(value) {
        if (!value) {
            return "";
        }
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return "";
        }
        return new Intl.DateTimeFormat("bg-BG", {
            weekday: "short",
            day: "numeric",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
        }).format(date);
    }

    function addMeta(label) {
        if (!label) {
            return;
        }
        const item = document.createElement("span");
        item.textContent = label;
        slideMeta.append(item);
    }

    function typeLabel(item) {
        return {
            announcement: item.priority === "urgent" ? "Важно съобщение" : "Съобщение",
            event: "Предстоящо събитие",
            substitution: "Заместване днес",
        }[item.type] || "School AI";
    }

    function renderSlide() {
        window.clearTimeout(rotationTimer);
        if (!slides.length) {
            feedContainer.hidden = true;
            emptyState.hidden = false;
            return;
        }
        feedContainer.hidden = false;
        emptyState.hidden = true;
        slideIndex = ((slideIndex % slides.length) + slides.length) % slides.length;
        const item = slides[slideIndex];

        slide.classList.remove("screen-slide");
        void slide.offsetWidth;
        slide.classList.add("screen-slide");
        slideType.textContent = typeLabel(item);
        slideTitle.textContent = item.title || "School AI";
        slideBody.textContent = item.body || (
            item.type === "substitution" ? "Проверете актуалната промяна за часа." : ""
        );
        slideMeta.replaceChildren();

        if (item.type === "announcement") {
            addMeta(item.category);
            if (item.publish_until) {
                addMeta(`До ${formatDateTime(item.publish_until)}`);
            }
        } else if (item.type === "event") {
            addMeta(formatDateTime(item.start_time));
            addMeta(item.room ? `Място: ${item.room}` : "");
        } else if (item.type === "substitution") {
            addMeta(item.subject);
            addMeta(item.replacement_teacher ? `Замества: ${item.replacement_teacher}` : "");
            addMeta(item.room ? `Кабинет: ${item.room}` : "");
        }
        slideCounter.textContent = `${slideIndex + 1} / ${slides.length}`;

        const configured = Number(config && config.settings && config.settings.screen_rotation_seconds);
        const seconds = Number.isFinite(configured) ? Math.max(5, Math.min(configured, 60)) : 12;
        progress.classList.remove("is-running");
        progress.style.animationDuration = `${seconds}s`;
        void progress.offsetWidth;
        progress.classList.add("is-running");
        rotationTimer = window.setTimeout(() => {
            slideIndex = (slideIndex + 1) % slides.length;
            renderSlide();
        }, seconds * 1000);
    }

    function cacheKey() {
        const audience = String(
            config && config.settings && config.settings.screen_audience || "all"
        ).toLocaleLowerCase("bg-BG");
        return `school-ai-public-feed:${audience}`;
    }

    function loadCachedFeed() {
        try {
            const cached = JSON.parse(localStorage.getItem(cacheKey()) || "null");
            if (cached && Array.isArray(cached.slides)) {
                slides = cached.slides;
                renderSlide();
                return cached;
            }
        } catch (error) {
            console.warn("Invalid cached public feed", error);
        }
        return null;
    }

    async function fetchFeed() {
        const cached = loadCachedFeed();
        const headers = new Headers({ Accept: "application/json" });
        if (cached && cached.revision) {
            headers.set("If-None-Match", `"${cached.revision}"`);
        }
        try {
            const response = await fetch("/api/screen/feed", {
                method: "GET",
                credentials: "same-origin",
                cache: "no-store",
                headers,
            });
            if (response.status === 304) {
                return;
            }
            if (response.status === 401) {
                location.replace("/pair?profile=screen");
                return;
            }
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const feed = await response.json();
            if (!Array.isArray(feed.slides)) {
                throw new Error("invalid_feed");
            }
            localStorage.setItem(cacheKey(), JSON.stringify(feed));
            slides = feed.slides;
            if (slideIndex >= slides.length) {
                slideIndex = 0;
            }
            renderSlide();
        } catch (error) {
            if (!cached) {
                slides = [];
                renderSlide();
            }
            console.warn("Could not refresh public screen feed", error);
        }
    }

    function clearPersonalScreen() {
        window.clearTimeout(personalTimer);
        personalOverlay.hidden = true;
        setText("[data-screen-person-name]", "");
        setText("[data-screen-person-detail]", "");
        setText("[data-screen-person-message]", "");
        setText("[data-screen-next-class]", "");
        setText("[data-screen-next-class-detail]", "");
        setText("[data-screen-message-count]", "0");
    }

    async function closePersonalSession(notifyServer) {
        if (notifyServer !== false) {
            try {
                await app.api("/api/screen/session/close", { method: "POST", body: {} });
            } catch (error) {
                console.warn("Could not close screen session", error);
            }
        }
        clearPersonalScreen();
    }

    function showPersonalScreen(data) {
        if (screenMode !== "paired" || !app.acceptEvent(data.event_id)) {
            return;
        }
        window.clearTimeout(personalTimer);
        setText("[data-screen-person-name]", data.name ? `Здравей, ${data.name.split(" ")[0]}!` : "Здравей!");
        setText(
            "[data-screen-person-detail]",
            [data.role, data.class_name].filter(Boolean).join(" · ")
        );
        setText("[data-screen-person-message]", data.message || "Няма нова лична информация.");

        const nextClass = data.next_class;
        if (nextClass) {
            setText("[data-screen-next-class]", nextClass.subject || "Следващ час");
            setText(
                "[data-screen-next-class-detail]",
                [
                    nextClass.start_time ? `${nextClass.start_time} ч.` : "",
                    nextClass.room ? `кабинет ${nextClass.room}` : "",
                    nextClass.class_name || "",
                ].filter(Boolean).join(" · ")
            );
        } else {
            setText("[data-screen-next-class]", "Няма предстоящ час");
            setText("[data-screen-next-class-detail]", "За днес няма друга информация.");
        }
        setText("[data-screen-message-count]", String(Number(data.pending_messages_count || 0)));
        personalOverlay.hidden = false;

        if (data.delivery_id) {
            window.setTimeout(() => {
                app.queueDeliveryAck("screen", data.delivery_id, data.message_ids || []);
            }, 650);
        }
        const configured = Number(config && config.settings && config.settings.kiosk_idle_seconds);
        const seconds = Number.isFinite(configured) ? Math.max(15, configured) : 60;
        personalTimer = window.setTimeout(() => closePersonalSession(true), seconds * 1000);
    }

    function handleSocketMessage(message) {
        if (message.type === "badge_detected" && message.data) {
            showPersonalScreen(message.data);
        } else if (message.type === "session_closed") {
            closePersonalSession(false);
        }
    }

    async function refreshConfiguration() {
        try {
            config = await app.api("/api/screen/bootstrap");
            app.applyDeviceConfig(config);
            screenMode = String(config.settings.screen_mode || "public").toLowerCase();
            await fetchFeed();
        } catch (error) {
            console.warn("Could not refresh screen configuration", error);
        }
    }

    function testScreen() {
        document.body.classList.add("is-testing");
        window.setTimeout(() => document.body.classList.remove("is-testing"), 1800);
    }

    personalCloseButton.addEventListener("click", () => closePersonalSession(true));
    personalOverlay.addEventListener("pointerdown", () => {
        if (!personalOverlay.hidden) {
            const configured = Number(config && config.settings && config.settings.kiosk_idle_seconds);
            const seconds = Number.isFinite(configured) ? Math.max(15, configured) : 60;
            window.clearTimeout(personalTimer);
            personalTimer = window.setTimeout(() => closePersonalSession(true), seconds * 1000);
        }
    });
    document.addEventListener("schoolai:paused", (event) => {
        if (event.detail.paused) {
            clearPersonalScreen();
            window.clearTimeout(rotationTimer);
        } else {
            renderSlide();
        }
    });
    document.addEventListener("schoolai:command", (event) => {
        const command = event.detail.command;
        if (command === "refresh_config") {
            refreshConfiguration();
        } else if (command === "test_screen") {
            testScreen();
        }
    });
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            clearPersonalScreen();
            window.clearTimeout(rotationTimer);
        } else {
            renderSlide();
            fetchFeed();
        }
    });
    window.addEventListener("pagehide", () => {
        clearPersonalScreen();
        window.clearTimeout(rotationTimer);
        window.clearInterval(feedRefreshTimer);
        if (profileController) {
            profileController.destroy();
        }
    });

    app.bootProfile("screen", handleSocketMessage)
        .then(async (controller) => {
            if (!controller) {
                return;
            }
            profileController = controller;
            config = controller.config;
            screenMode = String(config.settings.screen_mode || "public").toLowerCase();
            loadCachedFeed();
            await fetchFeed();
            feedRefreshTimer = window.setInterval(fetchFeed, 60_000);
        })
        .catch((error) => {
            console.warn("Screen startup failed", error);
            loadCachedFeed();
        });
}());
