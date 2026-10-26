# Fellow Stagg EKG Pro — local protocol notes (firmware 1.2.26)

Findings from reverse engineering a kettle on firmware **1.2.26 CLI** (build date Oct 8 2026),
cross-checked against [bramboe/stagg-ekg-plus-ha](https://github.com/bramboe/stagg-ekg-plus-ha)
and [montymhughes/stagg-ekg-pro](https://github.com/montymhughes/stagg-ekg-pro).
Everything here is **unauthenticated plain HTTP on port 80**: anyone on the LAN can control the kettle.

## Discovery

The kettle is an ESP32 (Espressif MAC OUI `5c:01:3b`). Scan the LAN for hosts answering `GET /temp`
with a JSON body containing `temp_set`.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Firmware page: `Current version: 1.2.26 CLI`, boot/running partitions, versions in each OTA slot |
| GET | `/temp` | `{"temp":35.17,"temp_set":94.00,"state":"S_Off","pwm":-10,"timestamp":1082528}`; `temp` is `nan` when lifted off the base |
| GET | `/api?i=0,p=0,d=0,t=3,s=1` | 17-byte binary settings record (below) |
| POST | `/api?i=0,p=0,d=0,t=3,s=1` | Write a 17-byte record; the mask selects which fields apply |
| GET | `/cli?cmd=<cmd>` | Legacy console. **On 1.2.26 output is muted** (only the HTML form returns) |

Observed states: `S_Off`, `S_StartupToTempr` (heating to target), `S_Heat`, `S_Hold`.

## Settings record

```
offset  size  field
0       2     mask (LE). Read: fields present. Write: fields to apply.
                0x0002 target temp   0x0004 schedule (time+temp)   0x0008 schedule ENABLED
                0x0010 clock         0x0020 clock mode  0x0040 hold minutes  0x0080 chime
                0x0100/0x0200 units  0x0400/0x0800 pre-boil  0x1000 language
2       2     altitude
4       2     target temp
6       2     schedule temp
8       1     schedule minute
9       1     schedule hour
10      1     clock minute
11      1     clock hour
12..15  1 ea  clock mode, hold minutes, chime level, language
16      1     revision (kettle increments on every change)
```

Temperatures: if bit 15 is set the value is **Celsius × 2** (`0x8000 | round(c*2)`), else Fahrenheit.

### Captured records

```
idle      f7 17 00 00 bc 80 c0 80 00 00 05 0d 00 1e 00 00 01   target 94 °C, sched 00:00 @ 96 °C off, clock 13:05
prepared  f7 17 00 00 bc 80 64 80 0d 0d 0a 0d 00 1e 00 00 03   sched 13:13 @ 50 °C, off
armed     ff 17 00 00 bc 80 64 80 0d 0d 0b 0d 00 1e 00 00 04   mask gains 0x0008
fired     f7 17 00 00 64 80 c0 80 00 00 0d 0d 00 1e 00 00 08   disarmed; target became 50 °C
```

## Scheduling (verified 2026-10-26)

* **Arm** in a single write: mask `0x000C`, schedule temp at 6–7, minute at 8, hour at 9.
  Example 06:30 @ 96 °C: `0c 00 00 00 00 00 c0 80 1e 06 00 00 00 00 00 00 00`.
* **Disarm**: same layout with mask `0x0004` (enable bit clear).
* The schedule runs on the **kettle's own clock**; set it with mask `0x0010` (minute at 10, hour at 11).
* When it fires the kettle disarms the schedule, copies the schedule temperature to the target,
  starts heating — and **drops off WiFi** while heating.
* It **rejoins WiFi by itself once back in `S_Off`** (observed 29 min later, right after a
  manual dial-off; no reboot — the `/temp` uptime counter was continuous, no power cycle needed).
  Not yet observed: rejoining unattended when the hold timer expires.
* There is no once/daily flag in the record. The `setsetting Repeat_sched` / `schedon` CLI writes
  that bramboe reports as working on 1.2.26 had **no observable effect on this build**, so the app
  treats every arm as one-shot and re-arms daily plans itself.

## Things to avoid

* Bare `ss`, `adcsamples`, `reset` reboot the kettle; `clrsettings`, `wifierase`, `provreset` wipe it.
* `heaton`/`heatoff` bypass the firmware's heating state machine.
* Firmware updates may change any of this. The app refuses to write to versions other than 1.2.26.
