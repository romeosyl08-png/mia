from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from bara.models import WorkConversation, Workspace
from bara.tools.visualizations import visualize_chart
from bara.views import _tools_schema


class ChartToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="chart-user", password="test-password")
        self.workspace = Workspace.objects.create(user=self.user, title="Charts")

    def test_bar_and_line_charts_return_bounded_structured_data(self):
        for chart_type in ("bar", "line"):
            with self.subTest(chart_type=chart_type):
                result = visualize_chart(
                    self.workspace,
                    "Évolution",
                    chart_type,
                    ["Janvier", "Février"],
                    [{"label": "Ventes", "values": [12, -2]}],
                )
                self.assertEqual(result["status"], "visualization_ready")
                self.assertEqual(result["chart"]["chart_type"], chart_type)
                self.assertEqual(result["chart"]["datasets"][0]["values"], [12.0, -2.0])

    def test_pie_chart_rejects_negative_values_and_multiple_series(self):
        labels = ["A", "B"]
        negative = visualize_chart(
            self.workspace,
            "Répartition",
            "pie",
            labels,
            [{"label": "Total", "values": [5, -1]}],
        )
        multiple = visualize_chart(
            self.workspace,
            "Répartition",
            "pie",
            labels,
            [
                {"label": "Premier", "values": [5, 1]},
                {"label": "Second", "values": [2, 4]},
            ],
        )
        self.assertIn("négatives", negative["error"])
        self.assertIn("une seule série", multiple["error"])

    def test_chart_rejects_non_finite_values_and_mismatched_labels(self):
        non_finite = visualize_chart(
            self.workspace,
            "Valeurs",
            "line",
            ["A", "B"],
            [{"label": "Série", "values": [1, float("nan")]}],
        )
        mismatched = visualize_chart(
            self.workspace,
            "Valeurs",
            "bar",
            ["A", "B"],
            [{"label": "Série", "values": [1]}],
        )
        self.assertIn("nombres finis", non_finite["error"])
        self.assertIn("une valeur par libellé", mismatched["error"])

    def test_chart_is_available_as_a_workspace_tool_and_site_preview_is_rendered(self):
        schemas, _ = _tools_schema(self.workspace)
        self.assertIn("visualize_chart", {item["function"]["name"] for item in schemas})

        self.client.force_login(self.user)
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        response = self.client.get(
            reverse("bara:conversation", args=[self.workspace.id, conversation.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="open-site-preview"')
        self.assertContains(response, 'id="site-preview-form"')
        self.assertContains(response, 'sandbox="allow-scripts"')
        self.assertContains(response, 'id="chart-prompt-button"')
