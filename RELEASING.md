# Releasing Kayak

Publishing is automated. Pushing a version tag builds `omaraflak/kayak` and
`omaraflak/kayak-sandbox` for amd64 and arm64 and pushes them to Docker Hub, which is
where every installed Kayak Launcher looks for updates.

## One-time setup

The workflow authenticates to Docker Hub with two repository secrets. **These are not
files.** GitHub stores them encrypted and injects them into the workflow at run time —
there is nothing to add to the repository, and nothing to commit.

### 1. Create a Docker Hub access token

1. Go to https://app.docker.com/settings/personal-access-tokens
2. **Generate new token**
3. Description: `github-actions`, permissions: **Read & Write**
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

Launchers track `latest`, so pushing the tag is what offers the update to everyone. They
notice within six hours, or immediately on their next start.

Progress is at https://github.com/omaraflak/kayak/actions. The first run takes a while
because nothing is cached yet; later runs reuse layers.

You can also trigger it by hand from the Actions tab (**Publish images** → **Run
workflow**), which publishes `latest` from whatever branch you pick.

## Checking it worked

```bash
docker pull omaraflak/kayak:latest
docker image inspect omaraflak/kayak:latest --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
```

That label is what the launcher shows users as the version number, so it should match the
tag you pushed.
