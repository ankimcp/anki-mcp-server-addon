# Test-only TLS fixtures

These files exist only so the tunnel proxy tests (`tests/unit/tunnel_proxy/`)
can run real TLS handshakes against local servers. The private key is public,
belongs to no real host, and must never be used for anything else.

| File | What it is |
| --- | --- |
| `test-ca.pem` | Self-signed test CA. The tests make the add-on trust exactly this CA. |
| `test-leaf.pem` | Server certificate signed by the CA. SANs: `relay.test`, `localhost`, `127.0.0.1`. |
| `test-leaf.key` | Private key of the leaf certificate (test-only, deliberately checked in). |
| `leaf.ext` | OpenSSL extensions used when signing the leaf. |

The CA's private key was deleted after signing, so the leaf can't be re-issued
by anyone; regenerate all four files together if you need a different leaf.
Validity is 100 years (`-days 36500`), so nothing here expires in practice.

## How they were generated

Run in this directory (`ca.key`, `leaf.csr` and `test-ca.srl` are scratch files, kept
out of the repo):

```sh
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out test-ca.pem \
  -days 36500 -subj "/CN=AnkiMCP unit-test CA (test only)" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash"

openssl req -newkey rsa:2048 -nodes -keyout test-leaf.key -out leaf.csr \
  -subj "/CN=relay.test"

openssl x509 -req -in leaf.csr -CA test-ca.pem -CAkey ca.key -CAcreateserial \
  -out test-leaf.pem -days 36500 -extfile leaf.ext

rm ca.key leaf.csr test-ca.srl
```

The extensions in `leaf.ext` (CA:FALSE, key usage, serverAuth, SKI/AKI, SANs)
are what Python's default `VERIFY_X509_STRICT` context needs to accept the
chain.
