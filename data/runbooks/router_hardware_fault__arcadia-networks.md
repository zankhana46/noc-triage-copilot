# Router Hardware Fault Runbook (Arcadia Networks)

## Symptoms
- ArcDirector NMS reports active critical chassis alarms (e.g., `ARC-HW-FAIL` or `MODULE_OFFLINE`) on an Arcadia TitanCore 9000 or EdgeReach 5000 series router, accompanied by an amber/red slot indicator LED.
- Physical interfaces hosted on the affected Service Line Card (SLC) experience continuous link state transitions (`DOWN`/`UP`), triggering rapid BFD peer drops and LACP bundle churn.
- Downstream nodes and subtended customer rings exhibit elevated packet loss, degraded throughput, and micro-bursting due to lost ECMP forwarding paths across the Arcadia backplane fabric.

## Probable causes
- Component-level ASIC lockup, transceiver bus fault, or localized memory failure on an Arcadia Service Line Card (SLC) or Route Processor Module (RPM).
- Unhandled ArcOS microcode exception or localized kernel panic causing the line card processor to become stuck in a perpetual watchdog reboot loop.
- Slot-level power drop or thermal cutoff caused by an internal fault on an Arcadia Power Entry Module (PEM) or backplane connector pin damage.

## Diagnostic steps
- Check chassis health and card status via CLI or NMS:
  - Run `show system chassis` and `show hardware module status` to inspect slot operational states, voltage levels, and thermal metrics.
  - Execute `show environment power` and `show system alarms` to verify if power allocation to the slot is within operational thresholds.
- Review crash logs and core dumps for the affected module:
  - Run `show logging module <slot-id>` and `show system core-dump slot <slot-id>` to determine if the card issued a panic string or hardware-interrupt fault code prior to failing.
- Determine if a redundant card or route exists:
  - Run `show redundancy status` to verify whether a standby RPM is in `HOT_STANDBY_SYNCED` state, and inspect `show ip route summary` to confirm alternative ECMP egress routes are absorbing traffic.

## Remediation
- Reset or reseat the affected card if safe to do so:
  - If traffic is diverted, attempt a soft reset via `hw-module reset slot <slot-id>`. If it remains unresponsive, dispatch an on-site technician to perform a physical unseat, backplane connector inspection, and clean reseat.
- Fail over to redundant hardware if available:
  - For active RPM degradation, execute `redundancy force-switchover` to transition forwarding operations to the standby engine with minimal control plane disruption.
- Replace the faulty card via field dispatch:
  - If the card fails POST or remains offline after a reseat, dispatch a field technician with an identical ArcCare-spared field replaceable unit (e.g., `ARC-SLC-100G-4P`) for a hot-swap replacement.

## Escalation
- Escalate immediately to Tier 2 (Core IP Engineering) if the chassis lacks a redundant RPM, alternative ECMP uplinks are fully saturated, or downstream cell-site gateways are unreachable.
- Open an emergency RMA and P1 ticket with Arcadia Networks Support (ArcCare TAC) if hardware faults recur on the same slot, or if diagnostic logs point to an unrecoverable backplane fabric bus failure.
