# Fiber Cut Runbook (Northeon)

## Symptoms

* **Link-down alarm on an aggregation or pre-aggregation fiber interface:** 
  * Northeon Axxis-series aggregation switches or EdgeNode-7000 units flag `LINK_DOWN` and `OPTICAL_LOS` (Loss of Signal) alarms on metro fiber interfaces (e.g., `100GE-0/1/0` or `10GE-0/2/1`).
* **Downstream sites report S1/NG interface failures and cell outages:** 
  * Connected cell sites register transport unreachable states, triggering `S1-C-PATH-FAIL` (LTE) or `NG-U-DOWN` (5G) critical alarms on Northeon Element Manager (NEM) and taking downstream sectors out of service.

## Probable causes

* **Physical fiber cut (construction, rodent damage):** 
  * Physical severance or severe shear of outside plant (OSP) aerial or buried fiber cables caused by uncoordinated civil construction, horizontal boring, or rodent chewing along the metro span.
* **Failed optical transceiver (SFP/SFP+):** 
  * Optical module failure (e.g., Northeon-certified 10G SFP+ or 100G QSFP28) exhibiting transmitter laser failure (`TX_FAULT`) or receiver photodiode degradation.
* **Patch panel or ODF fault:** 
  * Internal transport link failure caused by a broken, pinched, or contaminated optical jumper within the central office Optical Distribution Frame (ODF) or remote node patch cabinet.

## Diagnostic steps

* **Check optical power levels (Tx/Rx) on both ends of the span:** 
  * Execute `show interface <interface-id> optical-diagnostics` on the local Northeon node and the remote peer to evaluate Rx and Tx power levels. Confirm if Rx power reads below sensitivity limits (typically `<-30 dBm` or `None`).
* **Confirm whether the affected node has a redundant uplink; if so, verify failover:** 
  * Run `show redundancy protection-group aps` or `show erps-ring status` to check if Ethernet Ring Protection Switching (ERPS) successfully redirected traffic over the alternate path. Verify that secondary interface error counters (`show interface counters error`) remain clean.
* **Correlate with any scheduled fiber maintenance or third-party dig notifications:** 
  * Check the NOC change-management log and external GIS/One-Call ticketing maps to identify ongoing civil engineering, roadworks, or planned carrier-maintenance activities along the fiber run.
* **Use OTDR trace if available to localize the fault distance:** 
  * If the port utilizes an integrated optical diagnostic module, run `start otdr interface <interface-id> direction egress` to obtain the reflection break distance. Alternatively, request that on-site technicians plug a standalone OTDR into the ODF port to measure the exact kilometer marker of the fiber break.

## Remediation

* **If a redundant path exists, confirm traffic has failed over and monitor:** 
  * Verify that traffic flows through the protection path; if ERPS failed to trigger automatically, execute `protection-group aps switch-manual ring <id> to secondary` to isolate the cut span. Monitor downstream Northeon baseband controllers until cell site alarms clear to `NORMAL`.
* **Dispatch field technician to splice or replace the fiber span:** 
  * Dispatch the regional Outside Plant (OSP) contractor with the exact distance measurements from the OTDR trace to locate the break, dig/prep the cable, and execute emergency fusion splicing on all severed strands.
* **Swap suspect optical transceiver and re-verify link:** 
  * If the fiber core shows continuity with good power, replace the suspect Northeon optical transceiver with an on-site cold spare. Clear the interface diagnostics using `clear interface <interface-id> alarms` and verify that Tx/Rx power falls between standard thresholds (-8 dBm to -15 dBm).

## Escalation

* **No redundant path and outage exceeds 30 minutes: escalate to Tier 2 transport team:** 
  * If the affected site is homed on a non-redundant spur topology and fiber restoration has not commenced within 30 minutes, escalate to the Tier 2 Northeon Optical Transport Operations group and notify the regional OSP manager.
* **Multiple sites affected: open a major incident bridge:** 
  * If the fiber severance impacts multiple cell sites or an aggregation hub, immediately escalate to Incident Management to declare a Priority 1 event and launch an active Major Incident Management (MIM) bridge.
