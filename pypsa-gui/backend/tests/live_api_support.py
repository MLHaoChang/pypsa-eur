"""Opt-in paid-test accounting. Imported only by the stress-test module."""
from dataclasses import dataclass, field
import json

from harness.protocol import ProviderError
from harness.providers.openai_compat import OpenAICompatProvider


@dataclass
class TokenBudget:
    limit: int
    request_limit: int = 24
    charged: int = 0
    requests: int = 0
    records: list[dict] = field(default_factory=list)
    dollar_limit: float | None = None
    cost_nanodollars: int = 0
    # Verified standard long-context upper rates, including cache writes.
    # Dollars per million tokens -> nanodollars per token. No cache discount
    # is assumed for the spending gate.
    rates: dict = field(default_factory=lambda: {
        "gpt-6-luna": (250, 750),
        "gpt-6.1-sol": (5000, 15000),
        "gpt-6-astra": (25000, 75000),
    })
    on_change: object = None

    def reserve(self, input_estimate, output_limit, case, model=None):
        input_allowance = int(input_estimate * 1.25) + 512
        amount = input_allowance + output_limit
        cost = 0
        if self.dollar_limit is not None:
            if model not in self.rates:
                raise ProviderError("invalid_request", "live model pricing is not confirmed")
            input_rate, output_rate = self.rates[model]
            cost = input_allowance * input_rate + output_limit * output_rate
            if self.cost_nanodollars + cost > int(self.dollar_limit * 1e9):
                raise ProviderError("invalid_request", "live dollar budget exhausted before send")
        if self.requests >= self.request_limit or self.charged + amount > self.limit:
            raise ProviderError("invalid_request", "live test budget exhausted before send")
        self.charged += amount
        self.requests += 1
        self.cost_nanodollars += cost
        record = {"case": case, "reserved": amount, "charged": amount,
                  "usage_reported": False, "model": model,
                  "reserved_cost_nanodollars": cost, "charged_cost_nanodollars": cost}
        self.records.append(record)
        if self.on_change:
            self.on_change()
        return record

    def settle(self, record, usage):
        if not usage or record["usage_reported"]:
            return  # keep reservation on missing usage or duplicate settlement
        actual = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        self.charged += actual - record["charged"]
        if self.dollar_limit is not None:
            input_rate, output_rate = self.rates[record["model"]]
            actual_cost = usage.get("input_tokens", 0) * input_rate + usage.get("output_tokens", 0) * output_rate
            self.cost_nanodollars += actual_cost - record["charged_cost_nanodollars"]
            record["charged_cost_nanodollars"] = actual_cost
        record.update(charged=actual, usage_reported=True, usage=usage)
        if self.on_change:
            self.on_change()


class MeteredProvider(OpenAICompatProvider):
    def __init__(self, meter, encoder, case, *, choices=(), max_calls=4,
                 api_key=None, http_client=None):
        super().__init__("https://api.openai.com/v1", api_key=api_key,
                         http_client=http_client, token_param="max_completion_tokens")
        self.meter, self.encoder, self.case = meter, encoder, case
        self.choices, self.max_calls = choices, max_calls
        self.calls = 0
        self.requests = []
        self.current_record = None

    def _client(self):
        client = super()._client()
        if self._record_rejection not in client.event_hooks["response"]:
            client.event_hooks["response"].append(self._record_rejection)
        return client

    def _record_rejection(self, response):
        if response.status_code < 400:
            return
        from services.redaction import redact_secrets_in_str
        # Official API diagnostic bodies only; never record headers or keys.
        response.read()
        record = self.current_record
        record["http_status"] = response.status_code
        try:
            error = response.json().get("error", {})
            record["api_error"] = {key: redact_secrets_in_str(str(error[key]))[:1000]
                                   for key in ("code", "param", "message") if key in error}
        except (ValueError, AttributeError):
            pass
        if response.status_code in (400, 401, 403, 404, 429):
            self.meter.settle(record, {"input_tokens": 0, "output_tokens": 0})

    def _stream_payload(self, request, token_param):
        body = super()._stream_payload(request, token_param)
        if self.calls < len(self.choices) and self.choices[self.calls] is not None:
            choice = self.choices[self.calls]
            body["tool_choice"] = (choice if choice == "none" else
                {"type": "function", "name": choice} if "input" in body else
                {"type": "function", "function": {"name": choice}})
        return body

    def stream(self, request):
        if self.calls >= self.max_calls:
            raise ProviderError("invalid_request", "live case request limit reached")
        try:
            yield from super().stream(request)
        finally:
            self.calls += 1

    def _stream_once(self, request, token_param):
        # Reserve every HTTP attempt, including the adapter's token-parameter retry.
        body = self._stream_payload(request, token_param)
        estimate_body = body
        if "previous_response_id" in body:
            from harness.providers.openai_compat import _to_openai_messages
            from harness.providers.openai_responses import payload
            estimate_body = payload(request, _to_openai_messages(request), {})
        estimate = len(self.encoder.encode(json.dumps(estimate_body)))
        if "previous_response_id" in body:
            estimate += sum(r.get("usage", {}).get("output_tokens", 0)
                            for r in self.meter.records if r["case"] == self.case)
        record = self.meter.reserve(estimate, request.max_tokens, self.case, request.model)
        self.current_record = record
        self.requests.append(body)
        for event in super()._stream_once(request, token_param):
            if event.type == "message_done":
                self.meter.settle(record, event.usage)
            yield event
