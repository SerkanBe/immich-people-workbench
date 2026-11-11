"""Transport budgets remain cumulative through nested guard reconciliation."""
import time
import unittest
from unittest import mock

import people_workbench as app
import workspace_state as ws


class ValidationBudgetTests(unittest.TestCase):
    def client(self):
        client = app.Immich("https://synthetic.example", "synthetic-key")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = lambda amount: b" " * min(amount, 8)
        response.headers.get_content_type.return_value = "application/json"
        client.opener = mock.Mock()
        client.opener.open.return_value = response
        return client

    def test_nested_budget_keeps_counters_and_cannot_extend_deadline(self):
        client = self.client()
        deadline = time.monotonic() + 10
        with client.validation_budget(deadline):
            outer = client._validation_local.budget
            client.request("GET", "/synthetic-first")
            with client.validation_budget(deadline + 100):
                self.assertIs(client._validation_local.budget, outer)
                self.assertEqual(outer["deadline"], deadline)
                client.request("GET", "/synthetic-second")
            self.assertEqual((outer["reads"], outer["bytes"]), (2, 16))
        self.assertIsNone(client._validation_local.budget)

    def test_nested_budget_cannot_reset_raw_byte_or_request_limits(self):
        for counter, value in (("bytes", 64 * 1024 ** 2 - 4), ("reads", 1000)):
            with self.subTest(counter=counter):
                client = self.client()
                with client.validation_budget(time.monotonic() + 10):
                    client._validation_local.budget[counter] = value
                    with client.validation_budget(time.monotonic() + 10), self.assertRaises(ws.WorkspaceError) as error:
                        client.request("GET", "/synthetic-whitespace-response")
                    self.assertEqual(error.exception.code, "resource_limit")


if __name__ == "__main__":
    unittest.main()
