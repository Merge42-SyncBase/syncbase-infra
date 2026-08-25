# Third-party notices

SyncBase infrastructure is licensed under Apache-2.0. It assembles source,
container images, a database extension, a model, and runtime artifacts that
remain under their own upstream licenses.

| Component | Pinned identity | Use | Upstream license | Source |
| --- | --- | --- | --- | --- |
| `pgvector/pgvector` container | OCI digest `sha256:d2ef61f42ef767baa5a1475393303cc235bcd92febd9d7014eddb48b41f3bad0` | Local PostgreSQL/pgvector service and one-shot SQL clients | Image contains PostgreSQL and pgvector under their upstream licenses; it is not covered by SyncBase's Apache-2.0 license | <https://github.com/pgvector/pgvector> |
| `intfloat/multilingual-e5-small` | Model and tokenizer SHA-256 pins are owned by `syncbase-embedding` | Local embedding model | MIT | <https://huggingface.co/intfloat/multilingual-e5-small> |
| Microsoft ONNX Runtime | 1.26.0, with platform archive/library hashes owned by `syncbase-embedding` | Local inference shared library | MIT; copyright Microsoft Corporation | <https://github.com/microsoft/onnxruntime/tree/v1.26.0> |

The application images, Debian/Node/Nginx/Go base images, and the pgvector
image contain additional packages. Image digests identify bytes but do not
replace license attribution. The final release SBOM must enumerate the actual
five first-party images and their operating-system/runtime packages, and the
release manifest must bind that SBOM to the tested image digests.

See each sibling repository's `THIRD_PARTY_NOTICES.md` for application-level
dependencies. This file is a top-level attribution index, not a claim that the
Round-1 SBOM or public-image gate has passed.
