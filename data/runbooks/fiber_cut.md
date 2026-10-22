# Fiber Cut Runbook

## Symptoms
- **Link-down alarm on an aggregation or pre-aggregation fiber interface:** The router or switch interface transitions to a `down/down` operational state, raising Loss of Signal (LOS) or Loss of Frame (LOF) alarms in the NMS.
- **Downstream sites report S1/NG interface failures and cell outages:** Connected eNodeB and gNodeB nodes lose backhaul connectivity, generating S1-C/S1-U or NG-C/NG-U transport alarms and causing multiple downstream cell sites to enter an out-of-service state.

## Probable causes
- **Physical fiber cut (construction, rodent damage):** Total severance or crushing of the outside plant (OSP) fiber cable due to civil excavation, utility pole strikes, or rodent activity along the aerial/underground route.
- **Failed optical transceiver (SFP/SFP+):** Internal laser failure, degraded optical components, or physical damage to the transceiver module seated in the aggregation or pre-aggregation port.
- **Patch panel or ODF fault:** Severe microbends, accidental disconnections, damaged patch cords, or contaminated bulkheads at an intermediate Optical Distribution Frame (ODF).

## Diagnostic steps
- **Check optical power levels (Tx/Rx) on both ends of the span:**
  - Query digital optical monitoring (DOM/DDM) metrics via the CLI or NMS on both the local and remote interfaces.
  - Verify whether local Tx is normal while Rx indicates low-light or no-light conditions (typically below -30 to -40 dBm).
- **Confirm whether the affected node has a redundant uplink; if so, verify failover:** Check network topology diagrams and verify that protection switching (such as G.8032 ERP, BFD, or dynamic routing convergence) successfully rerouted production traffic without saturation on the secondary path.
- **Correlate with any scheduled fiber maintenance or third-party dig notifications:** Search active change management tickets and review local "Call Before You Dig" or civil construction notices matching the affected fiber route coordinates.
- **Use OTDR trace if available to localize the fault distance:** Trigger embedded or centralized Optical Time-Domain Reflectometer (OTDR) testing to identify the exact fault type (reflective break vs. high attenuation) and measure the optical distance (in meters) from the central office or shelter.

## Remediation
- **If a redundant path exists, confirm traffic has failed over and monitor:** Verify end-to-end service restoration across the backup link and continuously monitor interface utilization to prevent congestion drops while repairs proceed.
- **Dispatch field technician to splice or replace the fiber span:** Provide the outside plant (OSP) fiber repair crew with the OTDR fault distance and GIS path data. Instruct them to locate the physical break, re-splice the fiber cores in a suitable splice enclosure, and confirm insertion loss meets specification.
- **Swap suspect optical transceiver and re-verify link:** If the fiber plant tests clean, have an on-site technician clean the optical connectors and replace the SFP/SFP+ optic with a verified spare. Confirm the interface transitions to an `up/up` state with nominal Rx power levels.

## Escalation
- **No redundant path and outage exceeds 30 minutes:** Escalate directly to the Tier 2 Transport Engineering team and notify regional field operations management to prioritize emergency dispatch.
- **Multiple sites affected:** Immediately declare a P1/Major Incident, open a collaborative incident bridge, and engage the Incident Management team alongside optical vendor support.
