# Core Overload Runbook

## Symptoms
- **Elevated signalling latency at a core element (AMF/UPF/core router):** Signalling queues and round-trip times (RTT) on N1/N2/N3 or S1/S5 interfaces spike above established baselines. Transaction timeouts and interface retransmissions increase significantly on the serving node.
- **Attach/registration failures across many sites served by that element:** A simultaneous rise in initial registration, service request, and PDU session establishment drops occurs across all gNodeBs/eNodeBs routed through the affected core element. Disconnected subscribers receive NAS reject causes such as "Congestion" or encounter silent drops.

## Probable causes
- **Traffic surge beyond provisioned capacity:** Flash-crowd events, regional cell recoveries, or transport reconvergence events are triggering a massive wave of concurrent registration attempts that exceed designed Transactions Per Second (TPS) limits.
- **Software/control-plane resource exhaustion:** High CPU utilization, memory leaks, or context table saturation (e.g., PFCP or GTP-C session limits) are degrading core control-plane worker threads.
- **Upstream dependency (e.g. HSS/UDM) slowness:** Latency or processing bottlenecks in centralized databases (UDM, AUSF, HSS, or PCF) cause request backpressure and thread starvation on the local AMF/MME.

## Diagnostic steps
- **Check CPU/memory/session-table utilization on the core element:** 
  - Access the Element Management System (EMS) or node CLI to review CPU core usage, free memory, and active GTP/PFCP session-table capacity.
  - Identify whether specific microservices or interface queues are bottlenecked.
- **Check for a correlated spike in attach/registration attempts:** 
  - Pull telemetry on incoming N1/N2/S1-MME message rates to see if inbound volume deviates from typical historical baselines.
  - Check for mass radio link failures or re-attach storms originating from specific tracking areas.
- **Check health of upstream dependencies the element relies on:** 
  - Verify outbound interface metrics toward authentication and policy elements (e.g., N8/N12 towards UDM/AUSF, S6a towards HSS).
  - Look for rising HTTP/2 5xx responses, Diameter timeout errors, or elevated response times from downstream databases.

## Remediation
- **Scale out or fail over to a standby core element if available:** Trigger horizontal auto-scaling (HPA/VNF scale-out) to provision additional processing capacity immediately. If pooling is configured, adjust DNS weights or NRF profiles to route new subscriber registrations to alternate AMFs/UPFs within the core pool.
- **Throttle or rate-limit non-critical signalling if sustained:** Apply Control Plane Overload Control (CPOC) or configure NAS-level backoff timers to reject lower-priority traffic at the gNodeB boundary. Prioritize emergency calls and active data sessions while shedding background registration attempts.
- **Restart the affected process/VM only as a last resort, coordinated with change mgmt:** If control-plane processes are deadlocked, execute a graceful restart of the specific failed microservice or VM. Coordinate directly with the Incident Commander to avoid causing a secondary registration surge on adjacent nodes.

## Escalation
- **Many sites affected and no failover available: open a major incident immediately:** If multiple sites report active service degradation and node-level redundancy cannot absorb the load, declare a Severity 1 incident and bring core Tier 3 engineering and vendor support (Nokia/Ericsson TAC) onto an emergency bridge.
- **Suspected upstream dependency failure: escalate to the owning team in parallel:** If root-cause diagnostics show bottlenecking at the UDM/HSS, AAA, or transport layer, page the owning subscriber-data or IP-core teams directly to investigate the upstream node in parallel.
