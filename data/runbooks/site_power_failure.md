# Site Power Failure Runbook

## Symptoms

* **Mains failure alarm, followed by battery-low alarm:** AC Mains Failure alarms trigger first across the EMS/NMS, followed by DC Low Voltage or Battery on Discharge alarms as the battery bank drains toward the low-voltage disconnect threshold.
* **Site-down alarm and cell outages after battery depletion:** Complete site communication loss occurs once the battery bank is exhausted, dropping all active carriers and raising critical Site Down alarms across the BSS/RAN domain.

## Probable causes

* **Commercial power outage:** Local utility grid failure, severe weather events, or unscheduled utility maintenance impacting the primary AC power feed.
* **Rectifier failure:** Controller malfunction, internal component damage, or capacity overload preventing the conversion of commercial AC power to the -48V DC bus.
* **Blown site breaker:** Tripped main AC service disconnect, blown sub-panel feed breakers, or tripped individual rectifier circuit breakers caused by power surges or short circuits.

## Diagnostic steps

* **Check utility outage maps for the site's service area:** Cross-reference the site coordinates with the local power utility provider's public or wholesale outage dashboard to verify grid-level disruptions and identify estimated restoration times.
* **Verify rectifier module status and output voltage remotely:** Access the power plant controller (via SNMP or web GUI) to review AC input status, total DC load current, active rectifier alarm codes, and individual module operational health.
* **Estimate remaining battery runtime from telemetry:** Compare current DC bus voltage and total amperage draw against site-specific battery capacity (Ah) curves to project exact minutes remaining before low-voltage disconnect (LVD).

## Remediation

* **Dispatch generator or portable power if runtime is insufficient:** If battery autonomy is projected to expire before utility restoration, immediately dispatch Field Services with a mobile generator to connect to the site's generator tap box.
* **Replace failed rectifier module:** If AC power is present but the DC plant is degraded, dispatch field personnel with pre-configured, compatible rectifier modules to hot-swap the faulty units and clear controller alarms.
* **Restore mains and confirm batteries begin recharging:** Once AC mains or generator power is restored, confirm remotely that DC bus voltage rises to nominal float/equalize levels and that battery current indicates a positive charging state.

## Escalation

* **Battery runtime under 30 minutes with no ETA on power:** Immediately escalate to the Incident Commander and Regional Field Operations Manager for emergency field intervention and priority mobile generator placement.
* **Repeated power failures at the same site:** Open a High-Priority Facilities ticket for the Power & Infrastructure Engineering team to audit local utility reliability, battery state-of-health, and site electrical panel integrity.
