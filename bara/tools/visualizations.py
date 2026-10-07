import math

from .registry import register_tool


MAX_CHART_LABELS = 12
MAX_CHART_DATASETS = 5
MAX_CHART_VALUE = 1_000_000_000_000


@register_tool(
    name="visualize_chart",
    description=(
        "Affiche un graphique interactif à partir de données visibles dans la conversation ou les fichiers. "
        "Choisis bar, line ou pie ; limite-toi à 12 catégories et 5 séries. Les camemberts acceptent une seule série."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 100},
            "chart_type": {"type": "string", "enum": ["bar", "line", "pie"]},
            "labels": {
                "type": "array",
                "items": {"type": "string", "maxLength": 50},
                "minItems": 2,
                "maxItems": MAX_CHART_LABELS,
            },
            "datasets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "maxLength": 50},
                        "values": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": MAX_CHART_LABELS,
                        },
                    },
                    "required": ["label", "values"],
                    "additionalProperties": False,
                },
                "minItems": 1,
                "maxItems": MAX_CHART_DATASETS,
            },
        },
        "required": ["title", "chart_type", "labels", "datasets"],
        "additionalProperties": False,
    },
)
def visualize_chart(workspace, title, chart_type, labels, datasets):
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        return {"error": "Le titre du graphique est requis et limité à 100 caractères."}
    if not isinstance(chart_type, str) or chart_type not in {"bar", "line", "pie"}:
        return {"error": "Type de graphique invalide."}
    if (
        not isinstance(labels, list)
        or not 2 <= len(labels) <= MAX_CHART_LABELS
        or any(not isinstance(label, str) or not label.strip() or len(label) > 50 for label in labels)
    ):
        return {"error": f"Fournis de 2 à {MAX_CHART_LABELS} libellés non vides, de 50 caractères maximum."}
    if (
        not isinstance(datasets, list)
        or not 1 <= len(datasets) <= MAX_CHART_DATASETS
        or any(not isinstance(dataset, dict) for dataset in datasets)
    ):
        return {"error": f"Fournis de 1 à {MAX_CHART_DATASETS} séries de données."}
    if chart_type == "pie" and len(datasets) != 1:
        return {"error": "Un graphique en secteurs accepte une seule série de données."}

    clean_datasets = []
    for dataset in datasets:
        if set(dataset) != {"label", "values"}:
            return {"error": "Une série ne peut contenir que son nom et ses valeurs."}
        label = dataset.get("label")
        values = dataset.get("values")
        if not isinstance(label, str) or not label.strip() or len(label) > 50:
            return {"error": "Chaque série doit avoir un nom non vide de 50 caractères maximum."}
        if not isinstance(values, list) or len(values) != len(labels):
            return {"error": "Chaque série doit contenir une valeur par libellé."}
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or abs(value) > MAX_CHART_VALUE
            or not math.isfinite(value)
            for value in values
        ):
            return {"error": "Les valeurs doivent être des nombres finis, de valeur absolue inférieure à 10^12."}
        clean_values = [float(value) for value in values]
        if chart_type == "pie" and any(value < 0 for value in clean_values):
            return {"error": "Les valeurs d'un graphique en secteurs ne peuvent pas être négatives."}
        clean_datasets.append({"label": label.strip(), "values": clean_values})

    if chart_type == "pie" and not any(clean_datasets[0]["values"]):
        return {"error": "Un graphique en secteurs doit contenir au moins une valeur positive."}

    chart = {
        "title": title.strip(),
        "chart_type": chart_type,
        "labels": [label.strip() for label in labels],
        "datasets": clean_datasets,
    }
    return {
        "status": "visualization_ready",
        "chart": chart,
        "message": f"Graphique « {chart['title']} » affiché.",
    }
