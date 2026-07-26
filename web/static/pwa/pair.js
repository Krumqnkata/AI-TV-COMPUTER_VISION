(function () {
    "use strict";

    const app = window.SchoolAI;
    const profile = document.body.dataset.profile === "screen" ? "screen" : "kiosk";
    const form = document.querySelector("[data-pair-form]");
    const tokenInput = form.elements.enrollment_token;
    const nameInput = form.elements.name;
    const identifierInput = form.elements.identifier;
    const submitButton = document.querySelector("[data-pair-submit]");
    const statusBox = document.querySelector("[data-pair-status]");
    const cameraStage = document.querySelector("[data-pair-camera]");
    const video = document.querySelector("[data-pair-video]");
    const startButton = document.querySelector("[data-start-pair-camera]");
    const stopButton = document.querySelector("[data-stop-pair-camera]");

    let stream = null;
    let scanFrame = null;
    let zxingControls = null;
    let scanning = false;
    let lastNativeScan = 0;

    identifierInput.value = app.installationId(profile);
    nameInput.value = app.suggestedDeviceName(profile);

    function showStatus(message, isError) {
        statusBox.hidden = false;
        statusBox.classList.toggle("is-error", Boolean(isError));
        statusBox.textContent = message;
    }

    function extractToken(rawValue) {
        const value = String(rawValue || "").trim();
        if (value.startsWith("enr_")) {
            return value;
        }
        try {
            const parsed = JSON.parse(value);
            const token = String(parsed.enrollment_token || parsed.token || "").trim();
            return token.startsWith("enr_") ? token : "";
        } catch (_error) {
            return "";
        }
    }

    function stopCamera() {
        scanning = false;
        if (scanFrame) {
            cancelAnimationFrame(scanFrame);
            scanFrame = null;
        }
        if (zxingControls) {
            try {
                zxingControls.stop();
            } catch (_error) {
                // The stream may already be closed.
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
        cameraStage.hidden = true;
        stopButton.hidden = true;
        startButton.hidden = false;
    }

    async function tokenScanned(rawValue) {
        if (!scanning) {
            return;
        }
        const token = extractToken(rawValue);
        if (!token) {
            showStatus("QR кодът не е валиден код за сдвояване.", true);
            return;
        }
        tokenInput.value = token;
        stopCamera();
        showStatus("Кодът е прочетен. Сдвояваме устройството…", false);
        await submitPair();
    }

    async function nativeScanLoop(detector, timestamp) {
        if (!scanning) {
            return;
        }
        if (timestamp - lastNativeScan > 220 && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
            lastNativeScan = timestamp;
            try {
                const codes = await detector.detect(video);
                if (codes.length) {
                    await tokenScanned(codes[0].rawValue);
                    return;
                }
            } catch (error) {
                console.warn("Native barcode scan failed", error);
            }
        }
        scanFrame = requestAnimationFrame((nextTimestamp) => nativeScanLoop(detector, nextTimestamp));
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
        const detector = new BarcodeDetector({ formats: ["qr_code"] });
        scanFrame = requestAnimationFrame((timestamp) => nativeScanLoop(detector, timestamp));
    }

    async function startZxingScanner() {
        if (!window.ZXingBrowser || !window.ZXingBrowser.BrowserQRCodeReader) {
            throw new Error("scanner_not_available");
        }
        const reader = new window.ZXingBrowser.BrowserQRCodeReader(undefined, {
            delayBetweenScanAttempts: 250,
            delayBetweenScanSuccess: 750,
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
                    tokenScanned(result.getText());
                }
            }
        );
    }

    async function startCamera() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            showStatus(
                "Този браузър няма достъп до камера. Използвайте HTTPS или въведете кода ръчно.",
                true
            );
            return;
        }
        startButton.disabled = true;
        cameraStage.hidden = false;
        try {
            scanning = true;
            if ("BarcodeDetector" in window) {
                try {
                    await startNativeScanner();
                } catch (error) {
                    if (String(error && error.message) !== "qr_not_supported") {
                        console.warn("Falling back from BarcodeDetector", error);
                    }
                    stopCamera();
                    scanning = true;
                    cameraStage.hidden = false;
                    await startZxingScanner();
                }
            } else {
                await startZxingScanner();
            }
            startButton.hidden = true;
            stopButton.hidden = false;
            showStatus("Камерата е включена. Покажете QR кода в рамката.", false);
        } catch (error) {
            stopCamera();
            const insecure = !window.isSecureContext;
            showStatus(
                insecure
                    ? "Камерата изисква HTTPS връзка. Отворете защитения адрес на училищния сървър или въведете кода."
                    : "Не получихме достъп до камерата. Разрешете я от настройките на браузъра или въведете кода.",
                true
            );
            console.warn("Pairing camera failed", error);
        } finally {
            startButton.disabled = false;
        }
    }

    async function submitPair() {
        if (!form.reportValidity()) {
            return;
        }
        const token = tokenInput.value.trim();
        if (!extractToken(token)) {
            showStatus("Въведеният код не е валиден. Той трябва да започва с „enr_“.", true);
            tokenInput.focus();
            return;
        }

        submitButton.disabled = true;
        submitButton.textContent = "Сдвояване…";
        showStatus("Проверяваме еднократния код със сървъра…", false);
        try {
            const result = await app.api(`/api/${profile}/pair`, {
                method: "POST",
                headers: { "X-Enrollment-Token": token },
                body: {
                    identifier: identifierInput.value.trim(),
                    name: nameInput.value.trim(),
                    software_version: "pwa-1.1.0",
                },
            });
            tokenInput.value = "";
            showStatus(`Успешно сдвоено: ${result.device_name}. Отваряме приложението…`, false);
            window.setTimeout(() => {
                location.replace(profile === "screen" ? "/screen" : "/kiosk");
            }, 650);
        } catch (error) {
            const message = error instanceof app.ApiError
                ? error.message
                : "Сървърът не е достъпен. Проверете мрежовата връзка.";
            showStatus(message, true);
            submitButton.disabled = false;
            submitButton.textContent = "Сдвои устройството";
        }
    }

    startButton.addEventListener("click", startCamera);
    stopButton.addEventListener("click", stopCamera);
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        submitPair();
    });
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            stopCamera();
        }
    });
    window.addEventListener("pagehide", stopCamera);
}());
