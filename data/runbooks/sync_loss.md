# Sync Loss Runbook

## Symptoms

* **GPS/PTP sync-loss alarm at a site:** The Network Management System (NMS) triggers critical timing alarms (e.g., `SYNCLOSS`, `PTP_UNLOCKED`, or `GNSS_SIGNAL_LOST`) indicating the local node has lost its reference clock.
* **Handover failures and throughput degradation on that site's cells:** Cell sectors experience elevated call drops, inter-cell interference (particularly in TDD deployments), sharp increases in handover failure rates, and degraded user throughput due to frame-timing drift.

## Probable causes

* **GPS antenna/cable fault or obstruction:** Physical damage to the coaxial feedline, water ingress at RF connectors, lightning arrestor failure, or clear-sky obstruction blocking line-of-sight satellite tracking.
* **PTP grandmaster unreachable:** Transport network outages, intermediate routing failures, or excessive Packet Delay Variation (PDV) and jitter preventing the node from locking onto PTP packets.
* **Faulty sync card:** Internal hardware component failure, local oscillator degradation (OCXO/TCXO aging), or firmware corruption on the site's timing/synchronization unit.

## Diagnostic steps

* **Check GPS antenna cable and connector for damage:**
  * Query the node CLI to verify tracking status, satellite count (minimum 4 satellites required), and carrier-to-noise ratio ($C/N_0$).
  * Inspect the physical antenna, RF jumpers, surge arrestor, and connectors for physical damage, pinching, or moisture ingress.
* **Verify PTP grandmaster reachability and path delay:**
  * Test bidirectional IP reachability between the node and the Grandmaster IP using extended ping and trace-route tools.
  * Review PTP performance counters for one-way delay, packet jitter, and missing sync/follow-up messages.
* **Check how long the node has been free-running and its holdover accuracy:**
  * Query the node clock state (`show sync status` or vendor equivalent) to confirm if the system is in `Holdover` or `Free-Run` mode.
  * Determine the elapsed time in holdover and compare it against the oscillator’s maximum phase error budget (typically 1.5 µs for phase-aligned TDD).

## Remediation

* **Repair or replace GPS antenna/cable:** Reseat and weatherproof all RF connections, clear physical obstructions, or dispatch field maintenance to replace faulty GPS pucks and coaxial runs.
* **Re-point to an alternate PTP grandmaster if available:** Reconfigure the node's timing source priority list via CLI/EMS to lock onto the secondary or standby Grandmaster IP address.
* **Replace faulty sync card:** Issue a hardware dispatch for a site visit to swap the degraded timing module or baseband board, ensuring the replacement matches the hardware revision and firmware baseline.

## Escalation

* **Holdover accuracy degrading beyond spec:** Escalate immediately to Field Operations and the Tier 3 RAN engineering team when the node is within 30 minutes of exhausting its holdover budget to mitigate imminent cell shutdown and cluster-wide interference.
* **Multiple sites losing sync from the same grandmaster:** Escalate immediately to the Core Transport and IP/Sync Engineering team, as this indicates an upstream Grandmaster failure or regional transport backhaul degradation.
