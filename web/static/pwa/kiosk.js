(function () {
    "use strict";

    const app = window.SchoolAI;
    const idleView = document.querySelector("[data-idle-view]");
    const sessionView = document.querySelector("[data-session-view]");
    const errorView = document.querySelector("[data-kiosk-error]");
    const errorText = document.querySelector("[data-kiosk-error-text]");
    const video = document.querySelector("[data-kiosk-video]");
    const cameraPlaceholder = document.querySelector("[data-camera-placeholder]");
    const cameraCaption = document.querySelector("[data-camera-caption]");
    const startButton = document.querySelector("[data-start-scanner]");
    const retryButton = document.querySelector("[data-retry-scanner]");
    const manualForm = document.querySelector("[data-manual-scan-form]");
    const closeButtons = Array.from(document.querySelectorAll("[data-close-session]"));
    const speakButton = document.querySelector("[data-speak-session]");
    const assistantForm = document.querySelector("[data-assistant-form]");
    const assistantAnswer = document.querySelector("[data-assistant-answer]");
    const assistantInput = assistantForm.elements.text_query;
    const assistantPicker = document.querySelector("[data-assistant-picker]");
    const assistantCategoryList = document.querySelector("[data-assistant-categories]");
    const assistantQuestionList = document.querySelector("[data-assistant-questions]");
    const assistantSuggestionStatus = document.querySelector("[data-assistant-suggestion-status]");
    const recipientSearchForm = document.querySelector("[data-recipient-search-form]");
    const recipientList = document.querySelector("[data-recipient-list]");
    const messageForm = document.querySelector("[data-message-form]");
    const messageStatus = document.querySelector("[data-message-status]");
    const messageText = messageForm.elements.text;
    const messageLength = document.querySelector("[data-message-length]");

    let profileController = null;
    let config = null;
    let stream = null;
    let zxingControls = null;
    let scanFrame = null;
    let scanning = false;
    let submittingToken = false;
    let lastNativeScan = 0;
    let idleTimer = null;
    let currentSpeechText = "";
    let currentSessionEvent = null;
    let assistantSuggestionGeneration = 0;
    let selectedAssistantQuestion = null;

    function setText(selector, value) {
        const element = document.querySelector(selector);
        if (element) {
            element.textContent = value || "";
        }
    }

    function roleLabel(role) {
        return {
            student: "Ученик",
            teacher: "Учител",
            admin: "Администратор",
        }[role] || "Потребител";
    }

    function showCameraMessage(message) {
        cameraCaption.textContent = message;
    }

    function stopScanner() {
        scanning = false;
        if (scanFrame) {
            cancelAnimationFrame(scanFrame);
            scanFrame = null;
        }
        if (zxingControls) {
            try {
                zxingControls.stop();
            } catch (_error) {
                // The scanner may already have released the stream.
            }
            zxingControls = null;
        }
        if (stream) {
            stream.getTracks().forEach((track) => track.stop());
            stream = null;
        }
        if (video.srcObject) {
            video.srcObject = null;
        }
        cameraPlaceholder.hidden = false;
        startButton.hidden = false;
        startButton.disabled = false;
    }

    async function nativeScanLoop(detector, timestamp) {
        if (!scanning) {
            return;
        }
        if (timestamp - lastNativeScan > 220 && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
            lastNativeScan = timestamp;
            try {
                const codes = await detector.detect(video);
                if (codes.length && scanning) {
                    await badgeTokenDetected(codes[0].rawValue);
                    return;
                }
            } catch (error) {
                console.warn("Native QR scan failed", error);
            }
        }
        if (scanning) {
            scanFrame = requestAnimationFrame((nextTimestamp) => nativeScanLoop(detector, nextTimestamp));
        }
    }

    async function startNativeScanner() {
        const formats = typeof BarcodeDetector.getSupportedFormats === "function"
            ? await BarcodeDetector.getSupportedFormats()
            : ["qr_code"];
        if (!formats.includes("qr_code")) {
            throw new Error("qr_not_supported");
        }
        stream = await navigator.mediaDevices.getUserMedia({
            video: {
                facingMode: { ideal: "environment" },
                width: { ideal: 1280 },
                height: { ideal: 720 },
            },
            audio: false,
        });
        video.srcObject = stream;
        await video.play();
        cameraPlaceholder.hidden = true;
        const detector = new BarcodeDetector({ formats: ["qr_code"] });
        app.reportDiagnostics("kiosk", {
            camera_status: "active",
            camera_permission: "granted",
            scanner_engine: "native",
        });
        scanFrame = requestAnimationFrame((timestamp) => nativeScanLoop(detector, timestamp));
    }

    async function startZxingScanner() {
        if (!window.ZXingBrowser || !window.ZXingBrowser.BrowserQRCodeReader) {
            throw new Error("scanner_not_available");
        }
        const reader = new window.ZXingBrowser.BrowserQRCodeReader(undefined, {
            delayBetweenScanAttempts: 250,
            delayBetweenScanSuccess: 1000,
        });
        zxingControls = await reader.decodeFromConstraints(
            {
                video: {
                    facingMode: { ideal: "environment" },
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                },
                audio: false,
            },
            video,
            (result) => {
                if (result && scanning) {
                    badgeTokenDetected(result.getText());
                }
            }
        );
        cameraPlaceholder.hidden = true;
        app.reportDiagnostics("kiosk", {
            camera_status: "active",
            camera_permission: "granted",
            scanner_engine: "zxing",
        });
    }

    async function startScanner() {
        if (scanning || submittingToken || !idleView || idleView.hidden) {
            return;
        }
        if (localStorage.getItem("school-ai-paused:kiosk") === "1") {
            return;
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            app.reportDiagnostics("kiosk", {
                camera_status: "unavailable",
                camera_permission: "unavailable",
            });
            showCameraMessage("Камерата изисква съвременен браузър и HTTPS. Може да използвате USB скенер.");
            startButton.textContent = "Камерата не е достъпна";
            return;
        }

        startButton.disabled = true;
        startButton.textContent = "Включване на камерата…";
        showCameraMessage("Подготвяме камерата…");
        app.reportDiagnostics("kiosk", { camera_status: "starting" });
        scanning = true;
        try {
            if ("BarcodeDetector" in window) {
                try {
                    await startNativeScanner();
                } catch (error) {
                    console.warn("BarcodeDetector fallback", error);
                    stopScanner();
                    scanning = true;
                    await startZxingScanner();
                }
            } else {
                await startZxingScanner();
            }
            startButton.hidden = true;
            showCameraMessage("Задръжте QR кода неподвижно в рамката");
        } catch (error) {
            stopScanner();
            const denied = error && (
                error.name === "NotAllowedError"
                || error.name === "SecurityError"
            );
            app.reportDiagnostics("kiosk", {
                camera_status: denied ? "denied" : "error",
                camera_permission: denied ? "denied" : "unknown",
            });
            startButton.textContent = "Опитай камерата отново";
            showCameraMessage(
                !window.isSecureContext
                    ? "Достъпът до камера изисква HTTPS връзка."
                    : "Разрешете камерата от настройките на браузъра или използвайте USB скенер."
            );
            console.warn("Kiosk camera failed", error);
        }
    }

    function normalizeDetection(data) {
        const person = data.person || {};
        return {
            eventId: data.event_id,
            name: person.name || data.name || "",
            role: person.role || data.role || "",
            className: data.class_name || "",
            message: data.message || "",
            nextClass: data.next_class || null,
            messageTexts: Array.isArray(data.messages_delivered) ? data.messages_delivered : [],
            messageCount: Number(
                data.pending_messages_count !== undefined
                    ? data.pending_messages_count
                    : (data.message_ids || []).length
            ),
            messageIds: Array.isArray(data.message_ids) ? data.message_ids : [],
            deliveryId: data.delivery_id || null,
        };
    }

    function renderNextClass(item) {
        const container = document.querySelector("[data-next-class]");
        container.replaceChildren();
        const heading = document.createElement("h2");
        const detail = document.createElement("p");
        if (!item) {
            heading.textContent = "Няма предстоящ час";
            detail.textContent = "За днес няма друга информация.";
        } else {
            heading.textContent = item.subject || "Следващ час";
            const parts = [];
            if (item.start_time) {
                parts.push(`${item.start_time} ч.`);
            }
            if (item.room) {
                parts.push(`кабинет ${item.room}`);
            }
            if (item.class_name) {
                parts.push(item.class_name);
            }
            detail.textContent = parts.join(" · ");
        }
        container.append(heading, detail);
    }

    function renderMessages(event) {
        const list = document.querySelector("[data-personal-messages]");
        list.replaceChildren();
        if (event.messageTexts.length) {
            event.messageTexts.forEach((text) => {
                const item = document.createElement("li");
                item.textContent = text;
                list.append(item);
            });
        } else if (event.messageCount) {
            const item = document.createElement("li");
            item.textContent = "Съобщенията са включени в обобщението по-горе.";
            list.append(item);
        }
        setText(
            "[data-message-count]",
            event.messageCount === 1 ? "1 съобщение" : `${event.messageCount} съобщения`
        );
    }

    function clearAssistantAnswer() {
        assistantAnswer.hidden = true;
        assistantAnswer.textContent = "";
    }

    function stopSpeech() {
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
        }
    }

    function speakText(text) {
        if (
            !text
            || !("speechSynthesis" in window)
            || !("SpeechSynthesisUtterance" in window)
        ) {
            return false;
        }
        try {
            stopSpeech();
            const utterance = new window.SpeechSynthesisUtterance(text);
            utterance.lang = "bg-BG";
            utterance.rate = 0.95;
            window.speechSynthesis.speak(utterance);
            return true;
        } catch (error) {
            console.warn("Kiosk speech synthesis failed", error);
            return false;
        }
    }

    function setAssistantSuggestionStatus(message) {
        assistantSuggestionStatus.textContent = message || "";
        assistantSuggestionStatus.hidden = !message;
    }

    function updateAssistantQuestionSelection() {
        assistantQuestionList.querySelectorAll("[data-assistant-question]").forEach((button) => {
            button.setAttribute(
                "aria-pressed",
                selectedAssistantQuestion && button.dataset.assistantQuestion === selectedAssistantQuestion.id
                    ? "true"
                    : "false"
            );
        });
    }

    function selectAssistantQuestion(question) {
        stopSpeech();
        currentSpeechText = "";
        selectedAssistantQuestion = question;
        assistantInput.value = question.query;
        clearAssistantAnswer();
        updateAssistantQuestionSelection();
        assistantInput.focus();
        assistantInput.setSelectionRange(question.query.length, question.query.length);
        assistantInput.scrollIntoView({ block: "nearest" });
        resetIdleTimer();
    }

    function renderAssistantQuestions(category) {
        assistantQuestionList.replaceChildren();
        category.questions.forEach((question) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "assistant-question-button";
            button.dataset.assistantQuestion = question.id;
            button.setAttribute("aria-pressed", "false");
            button.textContent = question.label;
            button.addEventListener("click", () => selectAssistantQuestion(question));
            assistantQuestionList.append(button);
        });
        updateAssistantQuestionSelection();
    }

    function activateAssistantCategory(categories, categoryId) {
        assistantCategoryList.querySelectorAll("[data-assistant-category]").forEach((button) => {
            button.setAttribute(
                "aria-pressed",
                button.dataset.assistantCategory === categoryId ? "true" : "false"
            );
        });
        const category = categories.find((item) => item.id === categoryId);
        if (category) {
            renderAssistantQuestions(category);
        }
    }

    function renderAssistantSuggestions(rawCategories) {
        const categories = Array.isArray(rawCategories)
            ? rawCategories.filter((category) => (
                category
                && category.id
                && Array.isArray(category.questions)
                && category.questions.length
            ))
            : [];
        assistantCategoryList.replaceChildren();
        assistantQuestionList.replaceChildren();
        if (!categories.length) {
            setAssistantSuggestionStatus(
                "Няма готови въпроси. Можете да въведете свой въпрос."
            );
            return;
        }
        setAssistantSuggestionStatus("");
        categories.forEach((category) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "assistant-category-button";
            button.dataset.assistantCategory = category.id;
            button.setAttribute("aria-pressed", "false");
            button.textContent = category.label;
            button.addEventListener("click", () => {
                activateAssistantCategory(categories, category.id);
                resetIdleTimer();
            });
            assistantCategoryList.append(button);
        });
        activateAssistantCategory(categories, categories[0].id);
    }

    function clearAssistantSuggestions() {
        assistantSuggestionGeneration += 1;
        selectedAssistantQuestion = null;
        assistantCategoryList.replaceChildren();
        assistantQuestionList.replaceChildren();
        setAssistantSuggestionStatus("");
        assistantPicker.hidden = true;
    }

    async function loadAssistantSuggestions() {
        const generation = ++assistantSuggestionGeneration;
        assistantPicker.hidden = false;
        assistantCategoryList.replaceChildren();
        assistantQuestionList.replaceChildren();
        setAssistantSuggestionStatus("Зареждаме готовите въпроси…");
        try {
            const result = await app.api("/api/kiosk/query-suggestions");
            if (generation !== assistantSuggestionGeneration || !currentSessionEvent) {
                return;
            }
            renderAssistantSuggestions(result.categories);
        } catch (_error) {
            if (generation !== assistantSuggestionGeneration || !currentSessionEvent) {
                return;
            }
            setAssistantSuggestionStatus(
                "Готовите въпроси не са достъпни. Можете да въведете въпрос ръчно."
            );
        }
    }

    function resetIdleTimer() {
        window.clearTimeout(idleTimer);
        const configured = Number(config && config.settings && config.settings.kiosk_idle_seconds);
        const timeoutSeconds = Number.isFinite(configured) ? Math.max(15, configured) : 60;
        idleTimer = window.setTimeout(() => closeSession(false), timeoutSeconds * 1000);
    }

    async function showSession(rawData) {
        const event = normalizeDetection(rawData);
        if (!app.acceptEvent(event.eventId)) {
            return;
        }
        stopScanner();
        stopSpeech();
        currentSessionEvent = event;
        currentSpeechText = event.message;

        idleView.hidden = true;
        errorView.hidden = true;
        sessionView.hidden = false;
        setText("[data-person-greeting]", event.name ? `Здравей, ${event.name.split(" ")[0]}!` : "Здравей!");
        setText(
            "[data-person-detail]",
            [roleLabel(event.role), event.className].filter(Boolean).join(" · ")
        );
        setText("[data-session-message]", event.message || "Няма нова лична информация.");
        renderNextClass(event.nextClass);
        renderMessages(event);
        switchTab("overview");
        resetIdleTimer();
        loadAssistantSuggestions();

        if (event.deliveryId) {
            window.setTimeout(() => {
                app.queueDeliveryAck("kiosk", event.deliveryId, event.messageIds);
            }, 650);
        }
    }

    function showError(message) {
        stopScanner();
        idleView.hidden = true;
        sessionView.hidden = true;
        errorView.hidden = false;
        errorText.textContent = message || "Опитайте с друг бадж или потърсете администратор.";
    }

    function resumeIdle(delay) {
        window.setTimeout(() => {
            errorView.hidden = true;
            sessionView.hidden = true;
            idleView.hidden = false;
            submittingToken = false;
            startScanner();
        }, delay || 0);
    }

    async function badgeTokenDetected(rawToken) {
        const badgeToken = String(rawToken || "").trim();
        if (submittingToken || badgeToken.length < 8) {
            return;
        }
        submittingToken = true;
        stopScanner();
        showCameraMessage("Проверяваме баджа…");
        try {
            const result = await app.api("/api/kiosk/detect", {
                method: "POST",
                body: {
                    badge_token: badgeToken,
                    timestamp: new Date().toISOString(),
                    confidence: 1,
                },
            });
            if (result.status === "success") {
                await showSession(result);
                submittingToken = false;
                return;
            }
            if (result.status === "ignored") {
                const message = result.reason === "kiosk_busy"
                    ? "Киоскът се използва от друг човек. Приключете текущата сесия."
                    : "Този бадж току-що беше прочетен. Изчакайте момент и опитайте пак.";
                showError(message);
                resumeIdle(2200);
                return;
            }
            showError(result.message || "Баджът не е разпознат.");
        } catch (error) {
            showError(
                error instanceof app.ApiError
                    ? error.message
                    : "Няма връзка със сървъра. Проверете училищната мрежа."
            );
        } finally {
            submittingToken = false;
        }
    }

    function clearPersonalDom() {
        currentSessionEvent = null;
        currentSpeechText = "";
        window.clearTimeout(idleTimer);
        stopSpeech();
        setText("[data-person-greeting]", "");
        setText("[data-person-detail]", "");
        setText("[data-session-message]", "");
        setText("[data-message-count]", "0 съобщения");
        document.querySelector("[data-personal-messages]").replaceChildren();
        document.querySelector("[data-next-class]").replaceChildren();
        clearAssistantSuggestions();
        clearAssistantAnswer();
        assistantForm.reset();
        recipientSearchForm.reset();
        recipientList.replaceChildren();
        messageForm.reset();
        messageForm.hidden = true;
        messageStatus.hidden = true;
        messageStatus.textContent = "";
        messageLength.textContent = "0";
    }

    async function closeSession(notifyServer) {
        stopSpeech();
        currentSpeechText = "";
        if (notifyServer !== false) {
            try {
                await app.api("/api/kiosk/session/close", { method: "POST", body: {} });
            } catch (error) {
                console.warn("Could not close kiosk session on server", error);
            }
        }
        clearPersonalDom();
        sessionView.hidden = true;
        errorView.hidden = true;
        idleView.hidden = false;
        submittingToken = false;
        startScanner();
    }

    function switchTab(name) {
        document.querySelectorAll("[data-session-tab]").forEach((button) => {
            button.classList.toggle("is-active", button.dataset.sessionTab === name);
        });
        document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
            const active = panel.dataset.tabPanel === name;
            panel.hidden = !active;
            panel.classList.toggle("is-active", active);
        });
        if (name === "message" && !recipientList.children.length) {
            loadRecipients("");
        }
        resetIdleTimer();
    }

    async function submitAssistant(event) {
        event.preventDefault();
        const input = assistantForm.elements.text_query;
        const query = input.value.trim();
        if (!query) {
            return;
        }
        stopSpeech();
        currentSpeechText = "";
        const button = assistantForm.querySelector("button[type='submit']");
        button.disabled = true;
        assistantAnswer.hidden = false;
        assistantAnswer.textContent = "Търсим отговор…";
        try {
            const result = await app.api("/api/kiosk/query", {
                method: "POST",
                body: { text_query: query },
            });
            const answer = result.response || "Няма намерен отговор.";
            assistantAnswer.textContent = answer;
            currentSpeechText = answer;
            if (result.auto_speak === true) {
                speakText(answer);
            }
            resetIdleTimer();
        } catch (error) {
            assistantAnswer.textContent = error instanceof app.ApiError
                ? error.message
                : "Въпросът не може да бъде изпратен в момента.";
        } finally {
            button.disabled = false;
        }
    }

    function selectRecipient(person) {
        messageForm.elements.recipient_id.value = String(person.id);
        document.querySelector("[data-selected-recipient]").textContent = person.display_name;
        messageForm.hidden = false;
        messageText.focus();
        resetIdleTimer();
    }

    async function loadRecipients(query) {
        recipientList.replaceChildren();
        const loading = document.createElement("p");
        loading.textContent = "Зареждане…";
        recipientList.append(loading);
        try {
            const people = await app.api(`/api/kiosk/recipients?q=${encodeURIComponent(query || "")}`);
            recipientList.replaceChildren();
            if (!people.length) {
                const empty = document.createElement("p");
                empty.textContent = "Няма намерени активни потребители.";
                recipientList.append(empty);
                return;
            }
            people.forEach((person) => {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "recipient-button";
                const name = document.createElement("strong");
                name.textContent = person.display_name;
                const detail = document.createElement("small");
                detail.textContent = person.class_name || roleLabel(person.role);
                button.append(name, detail);
                button.addEventListener("click", () => selectRecipient(person));
                recipientList.append(button);
            });
        } catch (error) {
            recipientList.replaceChildren();
            const failed = document.createElement("p");
            failed.textContent = error instanceof app.ApiError
                ? error.message
                : "Получателите не могат да бъдат заредени.";
            recipientList.append(failed);
        }
    }

    async function submitMessage(event) {
        event.preventDefault();
        if (!messageForm.reportValidity()) {
            return;
        }
        const button = messageForm.querySelector("button[type='submit']");
        button.disabled = true;
        messageStatus.hidden = false;
        messageStatus.classList.remove("is-error");
        messageStatus.textContent = "Изпращане…";
        try {
            const result = await app.api("/api/kiosk/messages", {
                method: "POST",
                body: {
                    recipient_id: Number(messageForm.elements.recipient_id.value),
                    text: messageText.value.trim(),
                    valid_hours: 24,
                },
            });
            messageStatus.textContent = `Съобщението до ${result.recipient_name} е изпратено.`;
            messageText.value = "";
            messageLength.textContent = "0";
            resetIdleTimer();
        } catch (error) {
            messageStatus.classList.add("is-error");
            messageStatus.textContent = error instanceof app.ApiError
                ? error.message
                : "Съобщението не може да бъде изпратено.";
        } finally {
            button.disabled = false;
        }
    }

    function speakSession() {
        if (speakText(currentSpeechText)) {
            resetIdleTimer();
        }
    }

    function handleSocketMessage(message) {
        if (message.type === "badge_detected" && message.data) {
            showSession(message.data);
        } else if (message.type === "unknown_badge") {
            showError("Баджът не е разпознат или вече не е активен.");
            resumeIdle(2200);
        } else if (message.type === "session_closed") {
            closeSession(false);
        }
    }

    async function refreshConfiguration() {
        try {
            config = await app.api("/api/kiosk/bootstrap");
            app.applyDeviceConfig(config);
            resetIdleTimer();
        } catch (error) {
            console.warn("Could not refresh kiosk configuration", error);
        }
    }

    startButton.addEventListener("click", startScanner);
    retryButton.addEventListener("click", () => resumeIdle(0));
    manualForm.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!manualForm.reportValidity()) {
            return;
        }
        const input = manualForm.elements.badge_token;
        const token = input.value.trim();
        input.value = "";
        badgeTokenDetected(token);
    });
    closeButtons.forEach((button) => button.addEventListener("click", () => closeSession(true)));
    speakButton.addEventListener("click", speakSession);
    assistantForm.addEventListener("submit", submitAssistant);
    assistantInput.addEventListener("input", () => {
        stopSpeech();
        currentSpeechText = "";
        clearAssistantAnswer();
        if (
            selectedAssistantQuestion
            && assistantInput.value !== selectedAssistantQuestion.query
        ) {
            selectedAssistantQuestion = null;
            updateAssistantQuestionSelection();
        }
        resetIdleTimer();
    });
    recipientSearchForm.addEventListener("submit", (event) => {
        event.preventDefault();
        loadRecipients(recipientSearchForm.elements.q.value.trim());
    });
    messageForm.addEventListener("submit", submitMessage);
    messageText.addEventListener("input", () => {
        messageLength.textContent = String(messageText.value.length);
        resetIdleTimer();
    });
    document.querySelectorAll("[data-session-tab]").forEach((button) => {
        button.addEventListener("click", () => switchTab(button.dataset.sessionTab));
    });
    sessionView.addEventListener("pointerdown", resetIdleTimer);
    sessionView.addEventListener("keydown", resetIdleTimer);

    document.addEventListener("schoolai:paused", (event) => {
        if (event.detail.paused) {
            app.api("/api/kiosk/session/close", { method: "POST", body: {} }).catch(() => {});
            stopScanner();
            clearPersonalDom();
            sessionView.hidden = true;
            idleView.hidden = false;
        } else {
            startScanner();
        }
    });
    document.addEventListener("schoolai:command", (event) => {
        const command = event.detail.command;
        if (command === "refresh_config") {
            refreshConfiguration();
        } else if (command === "test_camera") {
            closeSession(true);
            showCameraMessage("Тест на камерата — покажете QR код в рамката");
        } else if (command === "test_audio" && "speechSynthesis" in window) {
            const utterance = new SpeechSynthesisUtterance("Звуковият тест на киоска е успешен.");
            utterance.lang = "bg-BG";
            window.speechSynthesis.speak(utterance);
        } else if (command === "test_screen") {
            showCameraMessage("Екранът на киоска работи.");
        }
    });

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            if (!sessionView.hidden) {
                app.api("/api/kiosk/session/close", {
                    method: "POST",
                    body: {},
                    keepalive: true,
                }).catch(() => {});
            }
            stopScanner();
            clearPersonalDom();
        } else if (idleView && !idleView.hidden) {
            startScanner();
        }
    });
    window.addEventListener("pagehide", () => {
        if (!sessionView.hidden) {
            app.api("/api/kiosk/session/close", {
                method: "POST",
                body: {},
                keepalive: true,
            }).catch(() => {});
        }
        stopScanner();
        clearPersonalDom();
        if (profileController) {
            profileController.destroy();
        }
    });

    app.bootProfile("kiosk", handleSocketMessage)
        .then((controller) => {
            if (!controller) {
                return;
            }
            profileController = controller;
            config = controller.config;
            startScanner();
        })
        .catch((error) => {
            showError(error instanceof Error ? error.message : "Киоскът не може да бъде стартиран.");
        });
}());
