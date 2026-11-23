# Implementation and follow-up work

The current implementation and evidence are recorded in [the parity report](parity-report.md).
PHP Ship leads the shared workflow; the spec owns ecosystem contracts. Differences
in common infrastructure settings should converge, while Python adapters, servers,
dependency handling and task integrations remain language-specific.

Implemented: four adapters, four servers, 23 optional services, canonical releases,
HTTP processes, external networks, environment merging, keyboard-driven setup,
frontend package managers, temporary build credentials and community extensions.

Remaining release gates:

- Publish the local shared-spec change before relying on its remote schema.
- Authorize and run candidate CI on the supported host/Python matrix.
- Exercise the candidate with the consuming deployment tool on a clean target.
- Review version/release notes and publishing setup separately.

Flower is deliberately excluded. Users may supply their own services and clients.
Additional frameworks/task systems remain possible through extensions and processes.
Infrastructure upgrades, backup restoration, high availability, hosted-provider
connectivity and arbitrary application behavior require their own verification.
