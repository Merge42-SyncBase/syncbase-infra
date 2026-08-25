# Source-availability diagnostic — development evidence

The deployed API could authenticate, list four database documents, and return source metadata for an
active version. The matching raw-PDF request returned HTTP 404, while the local deployment's
`originals` volume contained zero files.

This is a scoped failure, not proof that every source is lost everywhere. It does prove that the
tested deployment cannot currently complete the judge path from a retrieval hit to the underlying
PDF. Until a frozen, self-contained environment passes that path, report and video claims must say
that results carry source coordinates, not that the sources were runtime-verified as retrievable.

No worker was started and the probe did not create, update, or delete corpus records. Document names,
identifiers, credentials, cookies, endpoint details, and response bodies were not retained.
