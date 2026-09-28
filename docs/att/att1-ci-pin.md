# ATT-1 CI pin

ATT-1 part-byte fetch, once it lands, is stdlib `imaplib` over TLS. CI for that path pins the interpreter, not Mini curl.

- Python is 3.12, the version in `.github/workflows/test.yml` (`python-version: "3.12"`).
- TLS is that interpreter's `ssl.OPENSSL_VERSION` from `ssl.create_default_context`. CI does not install or require a curl build.
- Mini `/usr/bin/curl` 8.7.1 is not a CI pin. Record that version only in the supervised live-check log. Curl 8.5.0 URL-form results are not proof for Mini curl 8.7.1.
- Do not block the `imaplib` client on curl-version skew. Native URL-form stays out of ATT-1 until a read-only `EXAMINE` plus `BODY.PEEK` transcript exists, which this curl does not send.
