# slink-import

A small web UI that imports images from direct links (e.g. `https://i.postimg.cc/...`) into a
[Slink](https://github.com/andrii-kryvoviaz/slink) instance. The server downloads each image and
uploads it to Slink through the external upload API, several at a time.

- Paste links (one per line), choose the account, Private/Public and an optional collection
- Upload to several Slink accounts by saving an API key for each
- Live progress per link, with the new Slink URL for each
- Failed links can be retried with one click or downloaded as `failed-links.txt`
- Links that were already imported are skipped (unless you tick "re-upload")
- Password protected; logs contain only one `OK`/`FAIL` line per image

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `SLINK_URL` | required | Public address of Slink, e.g. `https://i.hellish.dev`. Also sent as the `Origin` header, which Slink requires. |
| `SLINK_API_KEY` | optional | Slink API key (Profile → API Keys), shown as the built-in **Default** account. `SLINK_API_KEY_FILE` can point to a file instead. Leave empty to only use accounts added in the UI. |
| `UI_PASSWORD` | required | Password for the web UI. `UI_PASSWORD_FILE` also works. |
| `SESSION_SECRET` | random | Signs the login cookie. Set it (e.g. `openssl rand -hex 32`) to stay logged in across restarts. |
| `PORT` | `8080` | Port the server listens on. |
| `BASE_PATH` | `/import` | Path the UI is served under. Must match the nginx `location`. |
| `WORKERS` | `4` | Default number of parallel imports (changeable per import in the UI). |
| `MAX_WORKERS` | `16` | Upper limit for the per-import setting. |
| `DEFAULT_PUBLIC` | `false` | Default visibility in the form. |
| `DEFAULT_COLLECTION` | empty | Default collection ID or URL in the form. |
| `MAX_IMAGE_MB` | `50` | Images larger than this are rejected. |
| `PUID` / `PGID` | `10001` | User and group the app runs as. Set them to the owner of the data directory (`id -u` / `id -g` on the host) when using a bind mount. On startup the data directory is handed to this user. |
| `DATA_DIR` | `/data` | Where `accounts.json` (saved API keys, mode 600), `collections.json` (saved collections), `jobs.json` (history) and `state*.json` (imported links per account) are stored. |

### Accounts

**Upload as** picks the Slink account to upload with. Use **Manage accounts** to add an account
by name and API key; keys are stored on the server and are never sent back to the browser (only
a hint like `sk_…ab12`). Removing an account also removes its saved collections and its
imported-links record; images already in Slink are not touched.

Each account has its own record of imported links, so the same link can be imported once per
account. The `SLINK_API_KEY` account keeps the history from before accounts existed.

### Collections

Collections are picked from a dropdown. Use **Manage** to save a collection under a name by
pasting its link (`/collection/<id>`) or ID once. Slink collections belong to one user, so each
saved collection is tied to the account it was saved for and only appears when that account is
selected. **Other link…** imports into an unsaved collection. Slink API keys can't list or create
collections, so create the collection in Slink first.

## Deploying with Portainer

The GitHub workflow publishes `ghcr.io/love-not-found/slink-import` (`latest` on every push to
`main`, plus a `sha-…` tag per commit) for amd64 and arm64.

1. Push to `main` and wait for the **Docker image** workflow to finish.
2. Make sure the package is public: GitHub → your profile → Packages → `slink-import` →
   Package settings → Change visibility. (If you keep it private, add `ghcr.io` as a registry in
   Portainer with a GitHub token that has `read:packages`.)
3. In Portainer: **Stacks → Add stack**, paste [`docker-compose.yml`](docker-compose.yml) into
   the web editor, and add the environment variables (at least `SLINK_URL` and `UI_PASSWORD`,
   ideally `SESSION_SECRET`, and optionally `SLINK_API_KEY`).
4. Deploy. To update later, use **Pull and redeploy** on the stack.

The UI is then available at `http://<server>:<PORT>/import/`.

## nginx

Add this to the existing `server { ... }` block of your Slink site. Note that `/upload` is
already used by Slink itself, which is why the default path is `/import`.

```nginx
location /import/ {
    # No trailing slash after the port: the /import prefix is passed through unchanged
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 5m;
}
```

Replace `127.0.0.1:8080` with the container's host and `PORT`. If you change `BASE_PATH`, change
the `location` to match. If nginx runs on the same machine, you can bind the port to localhost
only by changing the compose port mapping to `"127.0.0.1:${PORT:-8080}:${PORT:-8080}"`.

## Importing previous CLI state

To skip links imported with the old CLI script, copy its state file into the volume:

```sh
docker cp slink_import_state.json slink-import:/data/state.json
docker restart slink-import
```

## Local development

```sh
nix develop
SLINK_URL=https://i.hellish.dev SLINK_API_KEY_FILE=api.secret UI_PASSWORD=dev \
  DATA_DIR=./data python -m app
# or: nix run . (with the same environment variables)
```

Then open <http://localhost:8080/import/>.
