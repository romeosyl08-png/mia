(() => {

    const appearance =
        window.AI2AIAppearance;

    if (!appearance) {
        return;
    }

    function applyPersonaAppearance(persona) {
        if (typeof appearance.applyPersona === "function") {
            appearance.applyPersona(persona);
            return;
        }
        appearance.previewBotName(persona.appearance_name || appearance.get().botName);
        appearance.previewBotAvatar(persona.appearance_avatar || appearance.get().botAvatar);
        if (persona.appearance_accent) appearance.setAccent(persona.appearance_accent);
        if (persona.appearance_theme) appearance.setTheme(persona.appearance_theme);
    }


    const botNameInput =
        document.getElementById("botNameInput");

    const botAvatarInput =
        document.getElementById("botAvatarInput");

    const swatches =
        document.getElementById("swatches");

    const themeLight =
        document.getElementById("themeLight");

    const themeDark =
        document.getElementById("themeDark");

    const personaSelect =
        document.getElementById("personaSelect");

    const personaPrompt =
        document.getElementById("personaSystemPrompt");

    const personaMemory =
        document.getElementById("personaMemory");

    const personaTone =
        document.getElementById("personaTone");

    const personaTemp =
        document.getElementById("personaTemp");

    const personaLength =
        document.getElementById("personaLength");

    const personaSaveStatus =
        document.getElementById("personaSaveStatus");
    let appearanceSaveTimer = null;
    let activePersona = null;
    window.AI2AIPersonaSettings = {getSelected: () => activePersona};
    let schedulePersonaSave = () => {};

    function scheduleAppearanceSave(){
        window.clearTimeout(appearanceSaveTimer);
        appearanceSaveTimer = window.setTimeout(() => appearance.save(), 300);
    }

    function saveAppearance(){
        window.clearTimeout(appearanceSaveTimer);
        appearance.save();
    }


    /*
     * =========================
     * APPARENCE ACTUELLE
     * =========================
     */

    function render() {

        const data =
            appearance.get();


        if (botNameInput) {

            botNameInput.value =
                data.botName;

        }


        if (botAvatarInput) {

            botAvatarInput.value =
                data.botAvatar;

        }


        if (themeLight) {

            themeLight.classList.toggle(
                "active",
                data.theme === "light"
            );

        }


        if (themeDark) {

            themeDark.classList.toggle(
                "active",
                data.theme === "dark"
            );

        }


        if (swatches) {

            swatches
                .querySelectorAll(".swatch")
                .forEach(swatch => {

                    swatch.classList.toggle(
                        "active",
                        swatch.dataset.color === data.accent
                    );

                });

        }

    }


    /*
     * =========================
     * NOM
     * =========================
     */

    if (botNameInput) {

        botNameInput.addEventListener(
            "input",
            () => {
                appearance.previewBotName(botNameInput.value);
                if (activePersona) {
                    activePersona.appearance_name = botNameInput.value;
                    schedulePersonaSave();
                } else {
                    scheduleAppearanceSave();
                }
            }
        );

    }


    /*
     * =========================
     * AVATAR
     * =========================
     */

    if (botAvatarInput) {

        botAvatarInput.addEventListener(
            "input",
            () => {
                appearance.previewBotAvatar(botAvatarInput.value);
                if (activePersona) {
                    activePersona.appearance_avatar = botAvatarInput.value;
                    schedulePersonaSave();
                } else {
                    scheduleAppearanceSave();
                }
            }
        );

    }


    /*
     * =========================
     * BOUTONS EDIT
     * =========================
     */

    document
        .querySelectorAll(".edit-icon")
        .forEach(button => {

            button.addEventListener(
                "click",
                () => {

                    const input =
                        document.getElementById(
                            button.dataset.editTarget
                        );

                    if (!input) {
                        return;
                    }

                    input.readOnly = false;

                    input.focus();

                    input.select();

                }
            );

        });


    [botNameInput, botAvatarInput].forEach(input => {
        if (input) {
            input.addEventListener("blur", () => {
                input.readOnly = true;
                if (activePersona) schedulePersonaSave();
                else saveAppearance();
            });
        }
    });


    /*
     * =========================
     * COULEURS
     * =========================
     */

    if (swatches) {

        appearance
            .getAccents()
            .forEach(color => {

                const button =
                    document.createElement("button");

                button.type = "button";

                button.className = "swatch";

                button.dataset.color = color;

                button.style.background = color;

                button.title = color;


                button.addEventListener(
                    "click",
                    () => {

                        if (activePersona) {
                            activePersona.appearance_accent = color;
                            applyPersonaAppearance(activePersona);
                            schedulePersonaSave();
                        } else {
                            appearance.setAccent(color);
                        }

                        render();

                    }
                );


                swatches.appendChild(button);

            });

    }


    /*
     * =========================
     * THÈME
     * =========================
     */

    if (themeLight) {

        themeLight.addEventListener(
            "click",
            () => {

                if (activePersona) {
                    activePersona.appearance_theme = "light";
                    applyPersonaAppearance(activePersona);
                    schedulePersonaSave();
                } else {
                    appearance.setTheme("light");
                }

                render();

            }
        );

    }


    if (themeDark) {

        themeDark.addEventListener(
            "click",
            () => {

                if (activePersona) {
                    activePersona.appearance_theme = "dark";
                    applyPersonaAppearance(activePersona);
                    schedulePersonaSave();
                } else {
                    appearance.setTheme("dark");
                }

                render();

            }
        );

    }

    const defaultModelSelect = document.getElementById("defaultModelSelect");
    const defaultTopP = document.getElementById("defaultTopP");
    const defaultTopPValue = document.getElementById("defaultTopPValue");
    const defaultMaxTokens = document.getElementById("defaultMaxTokens");
    const preferencesSaveStatus = document.getElementById("preferencesSaveStatus");

    if (defaultModelSelect && defaultTopP && defaultMaxTokens) {
        const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
        let preferencesSaveTimer = null;

        function updateTopPLabel() {
            if (defaultTopPValue) {
                defaultTopPValue.textContent = Number(defaultTopP.value).toFixed(2).replace(".", ",");
            }
        }

        async function loadPreferences() {
            try {
                const response = await fetch("/chat/api/preferences/");
                if (!response.ok) throw new Error("Chargement des paramètres impossible.");
                const data = await response.json();
                defaultModelSelect.value = data.default_model_id || "";
                defaultTopP.value = String(data.top_p ?? 1);
                defaultMaxTokens.value = String(data.max_tokens ?? 500);
                updateTopPLabel();
            } catch (error) {
                if (preferencesSaveStatus) preferencesSaveStatus.textContent = error.message;
            }
        }

        async function savePreferences() {
            const tokenLimit = Number(defaultMaxTokens.value);
            if (!Number.isInteger(tokenLimit) || tokenLimit < 1 || tokenLimit > 8192) {
                if (preferencesSaveStatus) preferencesSaveStatus.textContent = "Choisis une limite entre 1 et 8192 tokens.";
                return;
            }
            try {
                const response = await fetch("/chat/api/preferences/", {
                    method: "PATCH",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken
                    },
                    body: JSON.stringify({
                        default_model_id: defaultModelSelect.value,
                        top_p: Number(defaultTopP.value),
                        max_tokens: tokenLimit
                    })
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error?.message || "Enregistrement impossible.");
                if (preferencesSaveStatus) preferencesSaveStatus.textContent = "Paramètres enregistrés.";
            } catch (error) {
                if (preferencesSaveStatus) preferencesSaveStatus.textContent = error.message;
            }
        }

        function schedulePreferencesSave() {
            if (preferencesSaveStatus) preferencesSaveStatus.textContent = "Enregistrement…";
            window.clearTimeout(preferencesSaveTimer);
            preferencesSaveTimer = window.setTimeout(savePreferences, 350);
        }

        defaultModelSelect.addEventListener("change", schedulePreferencesSave);
        defaultTopP.addEventListener("input", () => {
            updateTopPLabel();
            schedulePreferencesSave();
        });
        defaultMaxTokens.addEventListener("change", schedulePreferencesSave);
        loadPreferences();
    }


    if (personaSelect && personaPrompt && personaMemory && personaTone && personaTemp && personaLength) {

        let personas = [];
        let saveTimer = null;

        const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";

        function renderPersona(persona) {
            activePersona = persona;
            personaPrompt.value = persona.prompt || "";
            personaMemory.value = persona.memory || "";
            personaTone.value = persona.tone || "neutre";
            personaTemp.value = persona.temp ?? 0.7;
            personaLength.value = persona.length || "moyenne";
            applyPersonaAppearance(persona);
            const fallback = appearance.get();
            if (botNameInput) {
                const personaName = persona.appearance_name || fallback.botName;
                botNameInput.value = personaName.toLocaleLowerCase() === "le comptoir"
                    ? "2AI"
                    : personaName;
            }
            if (botAvatarInput) botAvatarInput.value = persona.appearance_avatar || fallback.botAvatar;
            render();
            const temperatureLabel = document.getElementById("tempVal");
            if (temperatureLabel) temperatureLabel.textContent = personaTemp.value;
            if (personaSaveStatus) personaSaveStatus.textContent = "";
            document.dispatchEvent(new CustomEvent('ai2ai:persona-selected', {detail: persona}));
        }

        async function loadPersonas() {
            try {
                const response = await fetch("/chat/api/personas/");
                if (!response.ok) throw new Error("Chargement des personas impossible.");
                personas = await response.json();
                const bigPersona = personas.find(persona => persona.is_big_persona);
                if (bigPersona && typeof appearance.setCentralAppearance === "function") {
                    appearance.setCentralAppearance(bigPersona);
                }
                personaSelect.replaceChildren();
                personas.forEach(persona => {
                    const option = document.createElement("option");
                    option.value = persona.id;
                    option.textContent = persona.name;
                    personaSelect.appendChild(option);
                });
                if (personas.length) {
                    personaSelect.value = String(personas[0].id);
                    renderPersona(personas[0]);
                } else {
                    personaSelect.disabled = true;
                    personaPrompt.disabled = true;
                    personaMemory.disabled = true;
                    personaTone.disabled = true;
                    personaTemp.disabled = true;
                    personaLength.disabled = true;
                    if (personaSaveStatus) personaSaveStatus.textContent = "Aucun persona disponible.";
                }
            } catch (error) {
                if (personaSaveStatus) personaSaveStatus.textContent = error.message;
                console.error("Erreur chargement persona", error);
            }
        }

        async function savePersona(persona = activePersona) {
            if (!persona) return;
            const payload = {
                prompt: personaPrompt.value,
                memory: personaMemory.value,
                tone: personaTone.value,
                temp: Number(personaTemp.value),
                length: personaLength.value,
                appearance_name: botNameInput?.value || "",
                appearance_avatar: botAvatarInput?.value || "",
                appearance_accent: persona.appearance_accent || "",
                appearance_theme: persona.appearance_theme || ""
            };
            try {
                const response = await fetch(`/chat/api/personas/${persona.id}/`, {
                    method: "PATCH",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken
                    },
                    body: JSON.stringify(payload)
                });
                if (!response.ok) throw new Error("Enregistrement impossible.");
                Object.assign(persona, await response.json());
                document.dispatchEvent(new CustomEvent('ai2ai:persona-saved', {detail: persona}));
                if (personaSaveStatus) personaSaveStatus.textContent = "Modifications enregistrées.";
            } catch (error) {
                if (personaSaveStatus) personaSaveStatus.textContent = error.message;
                console.error("Erreur sauvegarde persona", error);
            }
        }

        schedulePersonaSave = function () {
            if (personaSaveStatus) personaSaveStatus.textContent = "Enregistrement…";
            window.clearTimeout(saveTimer);
            const persona = activePersona;
            saveTimer = window.setTimeout(() => savePersona(persona), 500);
        };

        personaSelect.addEventListener("change", () => {
            window.clearTimeout(saveTimer);
            const selectedPersona = personas.find(persona => String(persona.id) === personaSelect.value);
            if (selectedPersona) renderPersona(selectedPersona);
        });

        personaPrompt.addEventListener("input", schedulePersonaSave);
        personaMemory.addEventListener("input", schedulePersonaSave);
        personaTone.addEventListener("change", schedulePersonaSave);
        personaTemp.addEventListener("input", () => {
            const temperatureLabel = document.getElementById("tempVal");
            if (temperatureLabel) temperatureLabel.textContent = personaTemp.value;
            schedulePersonaSave();
        });
        personaLength.addEventListener("change", schedulePersonaSave);
        loadPersonas();
    }


    render();

})();
