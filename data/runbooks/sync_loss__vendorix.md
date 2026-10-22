# Sync Loss Runbook (Vendorix)

## Symptoms
- Active critical alarm `ALM-SYNC-GNSS-FAIL` or `ALM-SYNC-PTP-LOS` raised against the site's Vendorix VMX-series Baseband Unit (BBU) in Vendorix NetManager, indicating clock reference drop.
- Immediate degradation of TDD frame synchronization leading to elevated inter-cell interference, sudden spikes in X2/Xn handover drop rates, and degraded downlink MIMO throughput across hosted sectors.

## Probable causes
- Physical damage, water ingress, or line-of-sight obstruction affecting the external Vendorix GNSS antenna puck or LMR feeder cable.
- Upstream transport failure, packet delay variation (PDV) spikes, or loss of IP reachability to the configured IEEE 1588v2 PTP Grandmaster.
- Hardware failure, oscillator drift, or improper seating of the Vendorix SYN-400 timing daughtercard in the VMX chassis.

## Diagnostic steps
- Check GPS antenna cable and connector for damage:
  - Run `vxcli show hardware gnss-receiver` to inspect active antenna bias current, SNR levels, and visible satellite count.
  - Inspect the N-type/SMA RF connector, surge arrestor, and downlead cable for physical stress, crimping, or water intrusion.
- Verify PTP grandmaster reachability and path delay:
  - Run `vxcli test ptp ping --gm-address <GM-IP>` and `vxcli show sync ptp status` to check mean path delay, offset from master, and packet jitter compliance.
- Check how long the node has been free-running and its holdover accuracy:
  - Run `vxcli show clock holdover-status` on the VMX BBU to retrieve accumulated phase error (must stay within ±1.5 µs for TDD) and total time elapsed since lock loss.

## Remediation
- Repair or replace GPS antenna/cable:
  - Clear obstructions, reseat and torque the antenna RF connectors, or replace faulty jumpers with standard Vendorix GNSS replacement kits, ensuring weatherproofing boots are reapplied.
- Re-point to an alternate PTP grandmaster if available:
  - Switch the primary timing profile via `vxcli config sync source set --priority 1 --type ptp-profile --ip <Secondary-GM-IP>` to restore lock via an alternate network path.
- Replace faulty sync card:
  - If diagnostic logs report unrecoverable local oscillator failure, dispatch field technicians to hot-swap the Vendorix SYN-400 timing module following standard ESD protocols.

## Escalation
- Holdover accuracy degrading beyond spec: escalate to Tier-3 RAN Operations and initiate priority field dispatch if holdover duration exceeds 6 hours or phase drift approaches ±1.2 µs, before frame slip triggers full cell outages.
- Multiple sites losing sync from the same grandmaster: escalate immediately to the Core Transport & Synchronization Team to investigate backhaul routing anomalies, MPLS jitter, or Grandmaster hardware failures.
