# ADR 0001: Use the kettle's native schedule, re-armed by the server

* Status: accepted
* Date: 2026-10-26

## Context

The goal is to start the Fellow Stagg EKG Pro at a set time each morning. On firmware 1.2.26:

* The HTTP CLI is muted and the CLI power/schedule commands showed no effect on our unit.
* The kettle drops off WiFi while heating, so community projects do not power it on over WiFi.
* Power on/off works over BLE, which needs an always-on BLE host near the kettle.
* The native settings record (`POST /api?...`) can set and arm the kettle's built-in schedule.
  Verified live: armed for 13:13, the kettle started heating at 13:13 on its own.

## Decision

The web app writes the kettle's **native schedule** over WiFi. The kettle fires on its own, so
nothing needs to be up at wake time. Because the record has no once/daily flag, every arm is
one-shot. For daily plans a background reconcile loop re-arms after each firing, once the kettle
is back online and idle. It never re-arms within 3 minutes after the wake time, so the kettle
cannot fire twice.

## Alternatives considered

* **Server timer + BLE power toggle.** Allows several schedules, but needs BLE hardware next to
  the kettle. Also the server must be up at wake time, and the power command is a toggle, so it
  is not idempotent.
* **Downgrade to 1.1.75SSP** (still in OTA slot 0) to get the full CLI back. Risks bricking the
  kettle, needs auto-updates blocked, and does not solve the WiFi-drop issue.
* **Home Assistant + bramboe integration.** A heavy dependency for a single feature.

## Consequences

* A daily plan depends on the kettle rejoining WiFi at some point between one firing and the
  next wake time. It was observed to rejoin as soon as it returned to idle (after a dial-off),
  with no power cycle. Rejoining after an unattended hold timeout is still to be confirmed.
* The kettle has a single schedule slot, so the app supports one wake time.
* The schedule follows the kettle clock. The app shows clock drift and offers a sync.
