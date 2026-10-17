# Security

See [the threat model and enforced boundaries](docs/security-model.md).

The host Python application, in-process native modules, browser broker and frame
providers are trusted. Importing a third-party Widget or pybind11 module is not a
sandbox. The browser renderer's sandbox is not a sandbox for the Python app.
Application IDs, message groups and shared-memory names are not OS identities.

Please do not publish exploit payloads, credentials or private application data in
an ordinary GitHub issue. Use the repository's GitHub private vulnerability reporting
feature if it is enabled. If it is unavailable, privately contact the repository
owner using a contact method they have explicitly published; this project does not
currently advertise a dedicated security mailbox or a guaranteed response SLA.

Include affected commit/runtime versions, OS, minimal reproduction, the specific
trust boundary crossed and expected behavior. Never attach real browser profiles,
cookies, API keys or screenshots containing private data.

Do not resolve compatibility problems by disabling the CEF sandbox or web security.
Old helpers without the matching security protocol are intentionally rejected.
