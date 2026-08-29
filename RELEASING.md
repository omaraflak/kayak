# Releasing Kayak

Publishing is automated. Pushing a version tag builds `omaraflak/kayak`,
`omaraflak/kayak-sandbox` and `omaraflak/kayak-audio` for amd64 and arm64 and pushes them
to Docker Hub, which is where every installed Kayak Launcher looks for updates.

## One-time setup

The workflow authenticates to Docker Hub with two repository secrets. **These are not
files.** GitHub stores them encrypted and injects them into the workflow at run time —
there is nothing to add to the repository, and nothing to commit.

### 1. Create a Docker Hub access token

1. Go to https://app.docker.com/settings/personal-access-tokens
2. **Generate new token**
3. Description: `github-actions`, permissions: **Read, Write & Delete**
   (Delete is what lets the workflow prune old version tags -- see "Retention"
   below. With a Read & Write token, publishing still works but the prune step
   fails and old tags accumulate.)
4. Copy the token. Docker Hub shows it once and never again.

### 2. Add both secrets to this repository

Go to https://github.com/omaraflak/kayak/settings/secrets/actions

Click **New repository secret** once per row:

| Name | Value |
| --- | --- |
| `DOCKERHUB_USERNAME` | `omaraflak` |
| `DOCKERHUB_TOKEN` | The token from step 1 |

The names must match exactly — the workflow looks them up by name. Once saved, GitHub
will never show you the values again, only let you overwrite them.

> If a token is ever pasted somewhere it should not be, revoke it on the Docker Hub page
> above and create a new one. Revoking is instant and only breaks the workflow until you
> paste the replacement.

## Cutting a release

```bash
git tag v1.0.0
git push origin v1.0.0
```

That publishes:

- `omaraflak/kayak:1.0.0`, `omaraflak/kayak:1.0`, `omaraflak/kayak:latest`
- the same three tags for `omaraflak/kayak-sandbox`
- the same three tags for `omaraflak/kayak-audio`, the speech runtime

The speech image is large (~1.8 GB) and is built for both architectures, so the
first release after a change to `Dockerfile.audio` or `audio_server/` is noticeably
slower. Releases that do not touch either reuse the cached layers.

Launchers track `latest`, so pushing the tag is what offers the update to everyone. They
notice within six hours, or immediately on their next start.

Progress is at https://github.com/omaraflak/kayak/actions. The first run takes a while
because nothing is cached yet; later runs reuse layers.

You can also trigger it by hand from the Actions tab (**Publish images** → **Run
workflow**), which publishes `latest` from whatever branch you pick.

## Retention

Only the newest **three** versions are kept, everywhere, and the cleanup runs
automatically as the last step of every publish:

- **Docker Hub**: after the images are pushed, the workflow deletes full
  version tags (`1.0.12`-style) older than the newest three, for all three
  repositories — `omaraflak/kayak`, `omaraflak/kayak-sandbox` and
  `omaraflak/kayak-audio` — by calling `scripts/prune_dockerhub_tags.py`. `latest`
  and the `major.minor` tags are never touched. This needs the Docker Hub token
  to have the **Delete** permission.
- **Launcher binaries**: the kayak-launcher release workflow deletes GitHub
  releases older than the newest three after each release publishes. Git tags
  are kept; only the releases and their assets go.

Nothing depends on the pruned artifacts: launchers track `latest` on Docker
Hub, and the launcher updater reads `latest.json` from the newest GitHub
release only. The three kept versions exist for manual rollback.

## Checking it worked

```bash
docker pull omaraflak/kayak:latest
docker image inspect omaraflak/kayak:latest --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
```

That label is what the launcher shows users as the version number, so it should match the
tag you pushed.
