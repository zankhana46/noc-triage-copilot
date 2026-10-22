# Router Hardware Fault Runbook

## Symptoms

- **Card/hardware failure alarm on a router:** The NMS generates major or critical chassis alarms indicating that a line card, fabric, or supervisor module is offline, degraded, or failing POST diagnostics.
- **Interface flapping on directly attached links:** Physical ports on the affected module rapidly transition between UP and DOWN states, triggering carrier transitions and link-state protocol renegotiations.
- **Degraded throughput on downstream nodes:** Downstream devices experience packet loss, high latency, or reduced capacity due to dropped transit traffic or suboptimal path convergence across the core.

## Probable causes

- **Failed line card or control module:** Internal component degradation, ASIC malfunction, or backplane bus communication errors have rendered the card non-operational.
- **Firmware crash/reboot loop:** Corrupted microcode, memory leaks, or software driver panics are causing the module to continuously crash and reboot.
- **Power supply fault within the chassis:** A degraded Power Supply Unit (PSU), failed power rail, or chassis backplane fault is delivering insufficient voltage to internal slots.

## Diagnostic steps

- **Check chassis health and card status via CLI or NMS:**
  - Execute platform health commands (e.g., `show module`, `show chassis environment`, or vendor equivalent) to check card operational states, temperature sensors, and power draw.
  - Review NMS telemetry to isolate the failure to a specific slot, PSU, or fabric module.
- **Review crash logs / core dumps for the affected module:**
  - Inspect syslog outputs and platform crash logs for kernel panics, parity errors, or hardware watchdog timeouts.
  - Verify whether core dumps were written to system storage for vendor forensic analysis.
- **Determine if a redundant card or route exists:**
  - Check supervisor/fabric redundancy status (e.g., SSO/NSF readiness) to confirm if standby hardware is available.
  - Review dynamic routing tables (OSPF/IS-IS/BGP) to ensure alternative transit paths can absorb diverted traffic without congestion.

## Remediation

- **Reset or reseat the affected card if safe to do so:**
  - Execute a soft reload or power-cycle of the specific module via CLI to clear transient hardware locks.
  - If soft recovery fails and a technician is on-site, perform a physical reseat of the module ensuring guide pins and ejector levers lock properly.
- **Fail over to redundant hardware if available:**
  - Force a manual switchover to the standby supervisor or fabric module if automatic hitless failover did not trigger.
  - Drain traffic away from the degraded module by adjusting interface metrics or BGP local preference to favor redundant paths.
- **Replace the faulty card via field dispatch:**
  - Open a field dispatch ticket to deploy an on-site technician with an exact model and revision spare.
  - Hot-swap the failed component using standard ESD precautions and verify post-replacement link stabilization.

## Escalation

- **No redundant hardware and multiple downstream sites degraded: escalate to Tier 2:** Escalate immediately to Tier 2 / Advanced Network Engineering and notify Incident Management if customer-impacting outages cannot be rerouted around the single point of failure.
- **Repeated hardware faults on the same chassis: open an RMA with vendor support:** Engage the OEM support TAC to open a high-priority RMA for replacement hardware if the failure reoccurs or if chassis backplane damage is identified.
