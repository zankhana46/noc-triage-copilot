# Cell Congestion Runbook

## Symptoms

* **High PRB utilization alarm on a cell:** Downlink or Uplink Physical Resource Block (PRB) utilization continuously exceeds defined critical thresholds (typically >85%), triggering automated performance alerts in the EMS/NMS.
* **RRC setup failures and throughput drop on the same cell:** A surge in RRC connection rejection rates occurs due to lack of radio resources, accompanied by degraded user-perceived throughput and lower CQI reporting across the sector.

## Probable causes

* **Organic traffic growth:** Sustained increase in regional subscriber density or shifting user behavior that progressively exceeds the baseline capacity profile of the cell.
* **Temporary event-driven demand spike:** Unplanned mass gatherings, sporting events, or transit disruptions concentrating a high volume of active devices into a single sector footprint.
* **Neighboring cell outage shifting load:** Surrounding sites or co-located frequency layers going offline, forcing neighboring user equipment (UEs) to reselect or hand over to the surviving cell.

## Diagnostic steps

* **Check PRB utilization trend over the past 24-72 hours:** Extract the PRB utilization graph over the last 1–3 days in the performance management tool to determine if this is a recurring busy-hour pattern or an isolated anomaly.
* **Check whether a neighboring cell is down and redirecting traffic:** Query the active alarm dashboard for offline cells, degraded sectors, or transmission alarms on adjacent sites within the immediate RF neighborhood.
* **Review subscriber count and busy-hour traffic mix:** Audit the active RRC connected user counts and user-plane throughput trends against historical busy-hour baselines to identify sudden traffic volume changes.

## Remediation

* **Enable additional carrier/sector capacity if available:** Remotely activate soft-locked carrier licenses, bring dormant carriers online, or enable secondary Carrier Aggregation (CA) layers on the cell.
* **Apply traffic steering to neighboring cells with spare capacity:**
  * Adjust idle-mode cell reselection parameters to prioritize underutilized adjacent frequencies.
  * Tweak handover margins (e.g., A3 offset, `CellIndividualOffset`) to offload edge subscribers to nearby co-coverage cells with low PRB utilization.
* **Queue a capacity upgrade request if growth is sustained:** If parameter adjustments do not resolve congestion and traffic growth persists across multiple busy periods, submit a formal capacity augmentation request via the ticketing system.

## Escalation

* **Sustained congestion beyond KPI threshold:** If PRB utilization remains above threshold for more than 48 hours and local tuning fails to resolve it, escalate the incident to RF and Capacity Planning for hardware expansion or permanent layer re-dimensioning.
* **Event-driven spike exceeding temporary capacity:** If localized demand completely saturates the sector and is projected to last through an ongoing major public event, escalate to Special Events and Field Operations to deploy a Cell on Wheels (COW) or Cell on Light Truck (COLT).
