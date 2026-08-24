# Test patterns for lake-rise-model

## Prefer behavior over implementation

- Assert on outputs and interaction boundaries (HTTP status/body, promoted artifact values,
  raised vs swallowed errors) rather than internal call sequences.
- Implementation should be able to change without breaking tests as long as behavior holds.

## Calibration pipeline

- **Version promotion:** build two sequential candidates with different `base_version` values
  and assert the later artifact inherits earlier parameter changes — not just the latest delta
  applied to v0.
- **Config threading:** pass config knobs (e.g. `min_recession_days`) through the public entry
  point (`train.train`, `service.run_training`) and assert behavior changes at the boundary
  (proposal present vs absent), not that a specific inner function received an argument.
- **HTTP approve:** set up pending state directly when the test target is auth/token gating;
  use `service.run_training` when the full train→approve flow is under test.

## Live HA / archive

- Mock `httpx.MockTransport` handlers per sensor; return non-2xx for rain history to verify
  upstream errors propagate instead of degrading to empty series.
- Keep prediction-path degradation tests separate from continuous-archive tests — archive
  appends must fail closed on rain outages.

## Alerting channels

- **SMTP notifier:** fake `smtplib.SMTP` with a small class (constructor + the methods
  actually called: `starttls`/`login`/`send_message`/`quit`) and `monkeypatch.setattr(smtplib,
  "SMTP", FakeClass)` — no network, no `with`-block assumptions in the fake.
- **Cleanup-after-success gotcha:** `send_message()` already waits for the server's final
  "250 OK", so the message is delivered once it returns; a subsequent `quit()` failure (real
  SMTP servers occasionally drop the connection before acking QUIT) is cleanup noise, not a
  delivery failure. Test this explicitly with a fake whose `quit()` raises but whose
  `send_message()` succeeds, asserting `send()` does *not* raise — the regression is that a
  swallowed-cleanup-exception bug elsewhere (`service._dispatch` treating any exception as
  "undelivered") turns a successful send into a duplicate retry next tick. Pair it with a fake
  whose `send_message()` raises, asserting the failure *does* propagate, so the two tests
  pin down exactly which phase is allowed to fail silently.

## Shared helpers

- Geometric recession fixtures (`_geometric_recession`) belong in the module that needs them;
  duplicate small setup blocks rather than importing across test files when the helper is
  domain-specific to one area.
