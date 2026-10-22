# Cooling Failure Runbook

## Symptoms

- **High-temperature alarm at a site or cabinet:** Environmental monitoring systems (EMS) or internal chassis sensors assert Major or Critical high-temperature alarms indicating cabinet or shelter ambient temperatures exceed operational limits.
- **Fan failure alarm, and possible equipment thermal shutdown:** Element Management System (EMS/NMS) telemetry reports fan tray failure alarms, accompanied by automated optical/line card thermal throttling or ungraceful equipment shutdowns initiated by thermal self-protection mechanisms.

## Probable causes

- **Failed cabinet/site cooling fan:** Internal fan tray hardware failure, degraded fan bearings, blown DC power distribution fuses, or fan controller board malfunction.
- **Blocked air filter or vents:** External intake louvers, exhaust grilles, or internal chassis air filters clogged with dust, debris, or physical obstructions restricting airflow.
- **HVAC unit failure:** Loss of commercial AC power to cooling systems, compressor failure, blown breaker, or thermostat controller malfunction on primary and backup shelter air conditioning units.

## Diagnostic steps

- **Check current cabinet/ambient temperature against threshold:** Query EMS/NMS sensor telemetry to check real-time ambient room and internal chassis temperatures, noting the rate of thermal rise and delta against the equipment's maximum operating threshold.
- **Verify fan RPM/status telemetry if available:** Execute node CLI diagnostics (e.g., `show environment fan` or equivalent) to confirm individual fan speeds, operational states, and whether fan trays are drawing expected power.
- **Check HVAC unit status for the site:** Inspect building management system (BMS) telemetry or auxiliary dry-contact alarms to determine HVAC operational state, current stage run-time, and active compressor/power failure alerts.

## Remediation

- **Clear blocked vents/filters:** Direct on-site personnel to inspect, clean, or replace choked chassis air filters and clear all external intake louvers and exhaust paths of physical obstructions.
- **Replace failed fan module:** Dispatch a field technician with an approved spare to hot-swap the degraded or failed fan tray assembly, verifying via CLI that new fans spin up to rated RPM and alarms clear.
- **Dispatch portable cooling if HVAC repair will take time:** Arrange emergency deployment of portable spot coolers or a mobile trailer HVAC unit to stabilize shelter ambient temperatures while dispatching a commercial HVAC contractor for permanent system repairs.

## Escalation

- **Temperature approaching equipment shutdown threshold:** Escalate immediately to the Field Operations Supervisor and Facilities Lead if internal temperatures reach within 5°C of the vendor-defined thermal shutdown limit and continue to climb.
- **Equipment has already shut down:** Declare a P1 Major Incident immediately upon node or card-level thermal shutdown, engaging incident management, network architecture on-call, and priority field dispatch to prevent cascading outages.
