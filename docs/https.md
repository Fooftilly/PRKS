# Serving PRKS over HTTPS (#1339)

PRKS can be reached over HTTPS in two ways:

- **Behind a reverse proxy (recommended for the internet).** A proxy such as
  [Caddy](https://caddyserver.com/) holds the certificate, renews it
  automatically and forwards plain HTTP to PRKS on a private address. Use this
  whenever PRKS is reachable from the internet.
- **PRKS's own TLS.** PRKS serves HTTPS itself from a certificate and key you
  give it. This fits a home network or a VPN with a certificate you already
  manage (an internal CA, or one your router or `mkcert` issues). PRKS does
  not obtain or renew certificates.

Do not expose PRKS over plain HTTP beyond a trusted network.

## PRKS's own TLS

Set both variables to PEM files the PRKS process can read:

| Variable | Purpose |
| -------- | ------- |
| `PRKS_TLS_CERT_FILE` | The certificate chain: the server certificate first, then any intermediates. |
| `PRKS_TLS_KEY_FILE` | The private key of that certificate, unencrypted. Keep it readable only by the PRKS user. |

They must be set together. At start PRKS loads the pair once; a missing,
unreadable or mismatched file, a file that is not PEM, or only one of the two
variables refuses to start (exit status 2). The error names the variable, never
the path or the file's contents.

With TLS on, PRKS treats the site as HTTPS:

- the session and sign-in device cookies are `Secure`;
- every response PRKS itself writes sends
  `Strict-Transport-Security: max-age=31536000` (browsers then refuse plain
  HTTP to that host name for a year; it does not cover other subdomains). The
  exception is the `400 Bad Request` Uvicorn sends on its own for a request it
  cannot parse (a malformed request line or headers, duplicate `Host`), before
  PRKS sees the request: that answer carries no HSTS. Browsers that already hold
  the header keep it, so this only matters if you need every response to carry
  it; put a proxy such as Caddy in front for that;
- the cross-site check expects the request's own `https` origin. Set
  `PRKS_PUBLIC_ORIGIN` only when browsers use a different name or port than
  the one PRKS serves.

Browsers reach PRKS by the name on the certificate. An IP literal or
`localhost` needs no further setting; a DNS name such as `prks.home.arpa` must
be listed in `PRKS_TRUSTED_HOSTS`, or PRKS answers 421 (it checks `Host` on
every request). The host of `PRKS_PUBLIC_ORIGIN`, when it is set, is accepted
without listing it there.

Plain HTTP is not served on the TLS port: one process speaks either HTTP or
HTTPS. HTTP/2 is not offered.

**Renewal needs a restart.** PRKS reads the certificate only at start.
After you replace the files, restart the web process (`docker compose restart
web`). Until then it keeps serving the old certificate, and browsers start
refusing it once it expires.

A direct run:

```bash
PRKS_TLS_CERT_FILE=/etc/prks/tls/cert.pem \
PRKS_TLS_KEY_FILE=/etc/prks/tls/key.pem \
python prks_app.py --host 0.0.0.0
```

### In Docker Compose

`docker-compose.yml` passes both variables to `web`, and its health check
switches to `https` when they are set. The files must be inside the container,
so mount them with an override file next to `docker-compose.yml` (Compose
reads `docker-compose.override.yml` by itself):

```yaml
# docker-compose.override.yml
services:
  web:
    volumes:
      - ./secrets/tls:/run/prks-tls:ro
    environment:
      PRKS_TLS_CERT_FILE: /run/prks-tls/cert.pem
      PRKS_TLS_KEY_FILE: /run/prks-tls/key.pem
```

Keep the files under `secrets/`, which Git and Docker builds ignore, never in
a tracked directory. The `web` container runs as `UID`:`GID` from `.env`, so
that user must be able to read `key.pem`. Publish the port as usual
(`PRKS_PUBLISH_HOST`) and open `https://<host>:8080`.

## Behind Caddy

Caddy gets and renews a public certificate for your domain by itself and
forwards requests to PRKS. PRKS then serves plain HTTP on a network only Caddy
reaches, and is told about the proxy:

- `PRKS_PUBLIC_ORIGIN`: the address browsers use, such as
  `https://prks.example.org`. Cookies become `Secure`, and the cross-site
  check expects this origin.
- `PRKS_TRUSTED_PROXIES`: Caddy's address only. PRKS believes
  `X-Forwarded-For` only from it, for the sign-in attempt limits. Anyone else
  who can reach the PRKS port from that address could spread sign-in attempts
  over invented addresses, so nothing else may reach it.

Caddy forwards the browser's `Host` header unchanged, and overwrites any
`X-Forwarded-For` a client sends (it trusts no upstream proxy by default), so
no further Caddy settings are needed.

### Compose example

This override adds a Caddy service on its own network with a fixed address,
and stops publishing the PRKS port on the host. DNS for `prks.example.org` must
point at this machine, and ports 80 and 443 must reach it (Caddy answers the
certificate authority's challenge on them).

```yaml
# docker-compose.override.yml
services:
  web:
    ports: !reset []
    networks:
      - default
      - edge
    environment:
      PRKS_PUBLIC_ORIGIN: https://prks.example.org
      PRKS_TRUSTED_PROXIES: 172.31.250.2

  caddy:
    # Pin a digest, as docker-compose.yml pins PostgreSQL.
    image: caddy:2.10
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    networks:
      edge:
        ipv4_address: 172.31.250.2
    depends_on:
      - web

networks:
  edge:
    ipam:
      config:
        - subnet: 172.31.250.0/29

volumes:
  caddy_data:
  caddy_config:
```

```caddyfile
# Caddyfile
prks.example.org {
	reverse_proxy web:8080
}
```

`ports: !reset []` needs Docker Compose 2.24 or later. Keep the `caddy_data`
volume: it holds Caddy's certificates and account key. The `edge` subnet is an
example; choose one that is free on the host, and keep
`PRKS_TRUSTED_PROXIES` equal to Caddy's address on it.

**With Docker Compose, run Caddy in Compose as above, not on the host.**
`PRKS_TRUSTED_PROXIES` must match the address PRKS sees as the connection's
peer, and through a published port that is not the host's `127.0.0.1`: Docker
forwards the connection, so `web` sees the bridge network's gateway (such as
`172.18.0.1`). That gateway is not Caddy's own address; trusting it would also
trust anything else Docker forwards through it, and trusting `127.0.0.1`
instead matches nothing, so every sign-in would count against one shared
address and users could throttle each other. The dedicated `edge` network gives
Caddy an address that is its alone.

Caddy on the host works for a **direct run** of PRKS (no Docker): run
`python prks_app.py --host 127.0.0.1`, point `reverse_proxy` at
`127.0.0.1:8080`, and set `PRKS_TRUSTED_PROXIES=127.0.0.1`. PRKS then sees
Caddy's connections from `127.0.0.1`, and believes Caddy's `X-Forwarded-For`
(`tests/pg/test_http_admission.py`, `ProxyTests`, checks exactly this
topology). Every local process can also reach the port as a trusted proxy, so
use this only on a machine you alone control.

Do not combine the two: with Caddy in front, leave `PRKS_TLS_CERT_FILE` and
`PRKS_TLS_KEY_FILE` unset.
