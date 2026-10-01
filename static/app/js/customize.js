(() => {

    const appearance =
        window.AI2AIAppearance;

    if (!appearance) {
        return;
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

    const personaTone =
        document.getElementById("personaTone");

    const personaTemp =
        document.getElementById("personaTemp");

    const personaLength =
        document.getElementById("personaLength");

    const personaSaveStatus =
        document.getElementById("personaSaveStatus");
    let appearanceSaveTimer = null;

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
                scheduleAppearanceSave();
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
                scheduleAppearanceSave();
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
                saveAppearance();
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

                        appearance.setAccent(color);

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

                appearance.setTheme("light");

                render();

            }
        );

    }


    if (themeDark) {

        themeDark.addEventListener(
            "click",
            () => {

                appearance.setTheme("dark");

                render();

            }
        );

    }


    if (personaSelect && personaPrompt && personaTone && personaTemp && personaLength) {

        let personas = [];
        let activePersona = null;
        let saveTimer = null;

        const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";

        function renderPersona(persona) {
            activePersona = persona;
            personaPrompt.value = persona.prompt || "";
            personaTone.value = persona.tone || "neutre";
            personaTemp.value = persona.temp ?? 0.7;
            personaLength.value = persona.length || "moyenne";
            const temperatureLabel = document.getElementById("tempVal");
            if (temperatureLabel) temperatureLabel.textContent = personaTemp.value;
            if (personaSaveStatus) personaSaveStatus.textContent = "";
        }

        async function loadPersonas() {
            try {
                const response = await fetch("/chat/api/personas/");
                if (!response.ok) throw new Error("Chargement des personas impossible.");
                personas = await response.json();
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

        async function savePersona() {
            if (!activePersona) return;
            const payload = {
                prompt: personaPrompt.value,
                tone: personaTone.value,
                temp: Number(personaTemp.value),
                length: personaLength.value
            };
            try {
                const response = await fetch(`/chat/api/personas/${activePersona.id}/`, {
                    method: "PATCH",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken
                    },
                    body: JSON.stringify(payload)
                });
                if (!response.ok) throw new Error("Enregistrement impossible.");
                activePersona = await response.json();
                if (personaSaveStatus) personaSaveStatus.textContent = "Modifications enregistrées.";
            } catch (error) {
                if (personaSaveStatus) personaSaveStatus.textContent = error.message;
                console.error("Erreur sauvegarde persona", error);
            }
        }

        function schedulePersonaSave() {
            if (personaSaveStatus) personaSaveStatus.textContent = "Enregistrement…";
            window.clearTimeout(saveTimer);
            saveTimer = window.setTimeout(savePersona, 500);
        }

        personaSelect.addEventListener("change", () => {
            const selectedPersona = personas.find(persona => String(persona.id) === personaSelect.value);
            if (selectedPersona) renderPersona(selectedPersona);
        });

        personaPrompt.addEventListener("input", schedulePersonaSave);
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