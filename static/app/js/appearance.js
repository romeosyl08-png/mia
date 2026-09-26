(function () {

    const STORAGE_KEY = "ai2ai-appearance";

    const DEFAULTS = {
        botName: "Le Comptoir",
        botAvatar: "🐘",
        accent: "#6C6FCB",
        theme: "light"
    };

    const ACCENTS = [
        "#6C6FCB",
        "#E8A33D",
        "#3F8E8A",
        "#B4483A",
        "#4E9F3D"
    ];


    function load() {

        try {

            const saved = JSON.parse(
                localStorage.getItem(STORAGE_KEY) || "null"
            );

            if (!saved) {
                return { ...DEFAULTS };
            }

            return {

                botName:
                    typeof saved.botName === "string" &&
                    saved.botName.trim()
                        ? saved.botName.trim()
                        : DEFAULTS.botName,

                botAvatar:
                    typeof saved.botAvatar === "string" &&
                    saved.botAvatar.trim()
                        ? saved.botAvatar.trim()
                        : DEFAULTS.botAvatar,

                accent:
                    ACCENTS.includes(saved.accent)
                        ? saved.accent
                        : DEFAULTS.accent,

                theme:
                    saved.theme === "dark"
                        ? "dark"
                        : "light"
            };

        } catch (error) {

            return { ...DEFAULTS };

        }
    }


    function save(data) {

        try {

            localStorage.setItem(
                STORAGE_KEY,
                JSON.stringify(data)
            );

        } catch (error) {}
    }


    function contrastColor(hex) {

        const value = hex.replace("#", "");

        const r = parseInt(value.slice(0, 2), 16);
        const g = parseInt(value.slice(2, 4), 16);
        const b = parseInt(value.slice(4, 6), 16);

        const luminance =
            (0.299 * r + 0.587 * g + 0.114 * b) / 255;

        return luminance > 0.62
            ? "#211914"
            : "#FFFFFF";
    }


    function darken(hex, amount = 0.16) {

        const value = hex.replace("#", "");

        return "#" + [0, 2, 4].map(index => {

            const channel = parseInt(
                value.slice(index, index + 2),
                16
            );

            return Math.max(
                0,
                Math.round(channel * (1 - amount))
            )
            .toString(16)
            .padStart(2, "0");

        }).join("");
    }


    let appearance = load();


    function apply() {

        document.documentElement.style.setProperty(
            "--accent",
            appearance.accent
        );

        document.documentElement.style.setProperty(
            "--accent-hover",
            darken(appearance.accent)
        );

        document.documentElement.style.setProperty(
            "--accent-ink",
            contrastColor(appearance.accent)
        );

        document.body.setAttribute(
            "data-theme",
            appearance.theme
        );


        /*
         * Éléments globaux
         */

        document.querySelectorAll(
            "[data-bot-name]"
        ).forEach(element => {

            element.textContent =
                appearance.botName;

        });


        document.querySelectorAll(
            "[data-bot-avatar]"
        ).forEach(element => {

            element.textContent =
                appearance.botAvatar;

        });


        /*
         * Titre navigateur
         */

        if (
            !document.body.hasAttribute(
                "data-keep-title"
            )
        ) {

            document.title =
                appearance.botName;

        }


        /*
         * Favicon
         */

        const favicon =
            document.getElementById("botFavicon");

        if (favicon) {

            const svg = `
                <svg
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 64 64"
                >
                    <text
                        x="32"
                        y="50"
                        text-anchor="middle"
                        font-size="48"
                    >
                        ${appearance.botAvatar}
                    </text>
                </svg>
            `;

            favicon.href =
                "data:image/svg+xml;charset=utf-8," +
                encodeURIComponent(svg);
        }
    }


    window.AI2AIAppearance = {

        get() {

            return {
                ...appearance
            };

        },


        save() {

            save(appearance);

            apply();

        },


        setBotName(name) {

            name = String(name || "").trim();

            appearance.botName =
                name || DEFAULTS.botName;

            save(appearance);

            apply();

        },


        setBotAvatar(avatar) {

            avatar = String(avatar || "").trim();

            appearance.botAvatar =
                avatar || DEFAULTS.botAvatar;

            save(appearance);

            apply();

        },


        setAccent(color) {

            if (!ACCENTS.includes(color)) {
                return;
            }

            appearance.accent = color;

            save(appearance);

            apply();

        },


        setTheme(theme) {

            if (
                theme !== "light" &&
                theme !== "dark"
            ) {
                return;
            }

            appearance.theme = theme;

            save(appearance);

            apply();

        },


        apply() {

            apply();

        },


        getAccents() {

            return [...ACCENTS];

        }

    };


    /*
     * Application immédiate sur toutes les pages
     */

    apply();

})();