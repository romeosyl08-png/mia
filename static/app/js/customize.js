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

                appearance.setBotName(
                    botNameInput.value
                );

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

                appearance.setBotAvatar(
                    botAvatarInput.value
                );

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


    render();

})();