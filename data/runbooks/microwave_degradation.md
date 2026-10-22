# Microwave Degradation Runbook

## Symptoms

- **Elevated bit-error-rate alarm on a microwave hop:** The radio management system reports persistent Pre-FEC/Post-FEC Bit Error Rate (BER), Errored Seconds (ES), or Severely Errored Seconds (SES) threshold violations. 
- **Intermittent packet loss and latency alarms on downstream nodes:** Downstream routers and cell site gateways display intermittent ICMP packet loss, jitter spikes, or traffic discards as Adaptive Coding and Modulation (ACM) shifts to lower-order states.

## Probable causes

- **Heavy rain/fog attenuation:** Severe hydrometeor scattering and rain fade temporarily absorbing RF energy, primarily impacting links running in higher frequency bands (e.g., 11 GHz to 80 GHz).
- **Antenna misalignment:** Structural shifting of the dish, loose bracket hardware, or tower twist caused by severe wind gusts, resulting in degraded main-lobe alignment.
- **Interference from a new co-channel source:** In-band RF interference introduced by an unauthorized emitter, overlapping carrier, or newly commissioned adjacent radio link operating without proper frequency coordination.

## Diagnostic steps

- **Check current and forecast weather at both hop ends:** Review live radar and meteorological feeds covering both near-end and far-end coordinates to confirm the presence of rain cells or localized dense fog.
- **Review received signal level (RSL) trend for drift vs. a sudden step change:** Pull the 24-hour RSL/MSE historical graph from the radio NMS. A sharp, permanent drop indicates physical antenna movement, whereas a gradual dip and recovery points to atmospheric attenuation.
- **Check for newly registered interference sources in the band:** Run an internal radio spectrum scan to inspect the noise floor and cross-reference spectrum management databases for recently approved neighboring links.

## Remediation

- **If weather-related, monitor; alarm typically clears as conditions improve:** Maintain link monitoring while the storm cell passes. Confirm ACM modulates down to sustain availability, and verify that RSL and BER baseline automatically once skies clear.
- **Re-align antenna if RSL shows a sudden step change:** Dispatch a tower crew to inspect mounts, verify feedhorn integrity, and re-peak the antenna azimuth and elevation against engineered design levels.
- **Coordinate frequency change if interference is confirmed:** Engage the spectrum coordination desk to allocate a clear alternative channel, then push the frequency/polarization change to both transceivers during an approved maintenance window.

## Escalation

- **BER remains high after weather clears:** Escalate the ticket to Tier-3 RF Engineering with exported 24-hour RSL, MSE, and error logs attached for link budget reassessment.
- **Service-affecting outage, not just degradation:** Immediately notify the Duty Incident Manager and page on-call Field Operations to initiate priority emergency dispatch.
